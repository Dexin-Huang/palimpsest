"""Train a compact MTHv2 glyph classifier from immutable tar shards."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import random
import sys
import tarfile
import time
from pathlib import Path

import torch
from PIL import Image
from torch import nn
from torch.optim.swa_utils import AveragedModel, get_ema_multi_avg_fn
from torch.utils.data import DataLoader, IterableDataset, get_worker_info
from torchvision import models, transforms

SEED = 361004
IMAGE_SIZE = 64
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def build_model(architecture: str, classes: int, *, pretrained: bool) -> nn.Module:
    if architecture == "resnet18":
        weights = models.ResNet18_Weights.DEFAULT if pretrained else None
        model = models.resnet18(weights=weights)
        model.fc = nn.Linear(model.fc.in_features, classes)
        return model
    if architecture == "mobilenet_v3_large":
        weights = models.MobileNet_V3_Large_Weights.DEFAULT if pretrained else None
        model = models.mobilenet_v3_large(weights=weights)
        model.classifier[3] = nn.Linear(model.classifier[3].in_features, classes)
        return model
    if architecture == "convnext_tiny":
        weights = models.ConvNeXt_Tiny_Weights.DEFAULT if pretrained else None
        model = models.convnext_tiny(weights=weights)
        model.classifier[2] = nn.Linear(model.classifier[2].in_features, classes)
        return model
    raise ValueError(f"unsupported architecture: {architecture}")


def image_transform(*, training: bool) -> transforms.Compose:
    operations: list[object] = []
    if training:
        operations.extend(
            [
                transforms.RandomAffine(
                    degrees=4,
                    translate=(0.05, 0.05),
                    scale=(0.9, 1.1),
                    fill=255,
                ),
                transforms.ColorJitter(brightness=0.15, contrast=0.15),
                transforms.RandomGrayscale(p=0.1),
            ]
        )
    operations.extend(
        [
            transforms.ToTensor(),
            transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
        ]
    )
    return transforms.Compose(operations)


class TarGlyphDataset(IterableDataset):
    def __init__(
        self,
        shards: list[Path],
        *,
        training: bool,
        seed: int,
        shuffle_buffer: int = 4096,
    ) -> None:
        super().__init__()
        self.shards = tuple(shards)
        self.training = training
        self.seed = seed
        self.shuffle_buffer = shuffle_buffer if training else 0
        self.epoch = 0
        self.transform = image_transform(training=training)

    def _samples(self, shards: list[Path]):
        for shard in shards:
            with tarfile.open(shard, "r") as archive:
                for member in archive:
                    if not member.isfile() or not member.name.endswith(".jpg"):
                        continue
                    label_text, separator, _ = member.name.partition("/")
                    if not separator or not label_text.isdecimal():
                        raise ValueError(f"invalid glyph member name: {member.name}")
                    source = archive.extractfile(member)
                    if source is None:
                        raise OSError(f"cannot read glyph member: {member.name}")
                    yield source.read(), int(label_text)

    def _decode(self, sample: tuple[bytes, int]) -> tuple[torch.Tensor, int]:
        payload, label = sample
        with Image.open(io.BytesIO(payload)) as image:
            return self.transform(image.convert("RGB")), label

    def __iter__(self):
        worker = get_worker_info()
        worker_id = worker.id if worker is not None else 0
        worker_count = worker.num_workers if worker is not None else 1
        shard_rng = random.Random(self.seed + self.epoch * 10_007)
        buffer_rng = random.Random(
            self.seed + self.epoch * 10_007 + worker_id * 1_000_003
        )
        shards = list(self.shards)
        if self.training:
            shard_rng.shuffle(shards)
        shards = shards[worker_id::worker_count]
        samples = self._samples(shards)
        if self.shuffle_buffer == 0:
            for sample in samples:
                yield self._decode(sample)
            return
        buffer: list[tuple[bytes, int]] = []
        for sample in samples:
            if len(buffer) < self.shuffle_buffer:
                buffer.append(sample)
                continue
            index = buffer_rng.randrange(len(buffer))
            displaced = buffer[index]
            buffer[index] = sample
            yield self._decode(displaced)
        buffer_rng.shuffle(buffer)
        for sample in buffer:
            yield self._decode(sample)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_torch_save(payload: object, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    torch.save(payload, temporary)
    temporary.replace(path)


def make_loader(
    dataset: TarGlyphDataset,
    *,
    batch_size: int,
    workers: int,
    drop_last: bool,
) -> DataLoader:
    options: dict[str, object] = {
        "batch_size": batch_size,
        "num_workers": workers,
        "pin_memory": True,
        "drop_last": drop_last,
    }
    if workers:
        options.update(prefetch_factor=3, persistent_workers=False)
    return DataLoader(dataset, **options)


def evaluate(
    model: nn.Module,
    loader: DataLoader,
    *,
    device: torch.device,
    classes: int,
) -> dict[str, float | int]:
    model.eval()
    total = 0
    top1 = 0
    top5 = 0
    class_total = torch.zeros(classes, dtype=torch.int64)
    class_correct = torch.zeros(classes, dtype=torch.int64)
    with torch.inference_mode():
        for images, labels in loader:
            images = images.to(
                device, non_blocking=True, memory_format=torch.channels_last
            )
            labels = labels.to(device, non_blocking=True)
            logits = model(images)
            predictions = logits.topk(5, dim=1).indices
            correct = predictions.eq(labels[:, None])
            total += labels.numel()
            top1 += int(correct[:, 0].sum())
            top5 += int(correct.any(dim=1).sum())
            labels_cpu = labels.cpu()
            class_total.scatter_add_(0, labels_cpu, torch.ones_like(labels_cpu))
            first_correct = correct[:, 0].to(torch.int64).cpu()
            class_correct.scatter_add_(0, labels_cpu, first_correct)
    observed = class_total > 0
    macro_top1 = (class_correct[observed] / class_total[observed]).mean().item()
    return {
        "samples": total,
        "top1": top1 / total,
        "top5": top5 / total,
        "macro_top1": macro_top1,
        "observed_classes": int(observed.sum()),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--architecture",
        choices=("resnet18", "mobilenet_v3_large", "convnext_tiny"),
        default="resnet18",
    )
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--weight-decay", type=float, default=1e-2)
    parser.add_argument("--seed", type=int, default=SEED)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.epochs <= 0 or args.batch_size <= 0 or args.workers < 0:
        raise ValueError(
            "epochs and batch size must be positive; workers cannot be negative"
        )
    args.output.mkdir(parents=True, exist_ok=True)
    classes_path = args.data / "classes.json"
    counts_path = args.data / "class_counts.json"
    report_path = args.data / "prep_report.json"
    characters = json.loads(classes_path.read_text(encoding="utf-8"))
    counts = json.loads(counts_path.read_text(encoding="utf-8"))
    prep_report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(characters, list) or len(characters) != len(counts):
        raise ValueError("classes.json and class_counts.json disagree")
    train_shards = sorted((args.data / "shards").glob("train-*.tar"))
    dev_shards = sorted((args.data / "shards").glob("dev-*.tar"))
    if not train_shards or not dev_shards:
        raise FileNotFoundError("training and development shards are required")

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.set_float32_matmul_precision("high")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda":
        raise RuntimeError("glyph training requires CUDA")

    train_dataset = TarGlyphDataset(train_shards, training=True, seed=args.seed)
    dev_dataset = TarGlyphDataset(dev_shards, training=False, seed=args.seed)
    train_loader = make_loader(
        train_dataset,
        batch_size=args.batch_size,
        workers=args.workers,
        drop_last=True,
    )
    dev_loader = make_loader(
        dev_dataset,
        batch_size=args.batch_size,
        workers=args.workers,
        drop_last=False,
    )

    model = build_model(args.architecture, len(characters), pretrained=True)
    model.to(device=device, memory_format=torch.channels_last)
    ema = AveragedModel(model, multi_avg_fn=get_ema_multi_avg_fn(0.9995))
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay
    )
    steps_per_epoch = prep_report["train_crops"] // args.batch_size
    total_steps = steps_per_epoch * args.epochs
    warmup_steps = max(1, round(total_steps * 0.03))

    def learning_rate_factor(step: int) -> float:
        if step < warmup_steps:
            return (step + 1) / warmup_steps
        progress = (step - warmup_steps) / max(1, total_steps - warmup_steps)
        return 0.5 * (1.0 + math.cos(math.pi * progress))

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, learning_rate_factor)
    class_weights = torch.tensor(counts, dtype=torch.float32).clamp_min(1).rsqrt()
    class_weights /= class_weights.mean()
    class_weights.clamp_(max=4.0)
    criterion = nn.CrossEntropyLoss(
        weight=class_weights.to(device), label_smoothing=0.05
    )
    scaler = torch.amp.GradScaler("cuda")
    log_path = args.output / "training.jsonl"
    best_key = (-1.0, -1.0)
    started = time.monotonic()
    source_identity = {
        "classes_sha256": sha256_file(classes_path),
        "class_counts_sha256": sha256_file(counts_path),
        "prep_report_sha256": sha256_file(report_path),
        "train_shards_sha256": {path.name: sha256_file(path) for path in train_shards},
        "dev_shards_sha256": {path.name: sha256_file(path) for path in dev_shards},
    }
    config = {
        "architecture": args.architecture,
        "classes": len(characters),
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "workers": args.workers,
        "learning_rate": args.learning_rate,
        "weight_decay": args.weight_decay,
        "seed": args.seed,
        "image_size": IMAGE_SIZE,
        "normalization": {"mean": IMAGENET_MEAN, "std": IMAGENET_STD},
        "label_smoothing": 0.05,
        "class_weight": "inverse_sqrt_frequency_mean1_max4",
        "ema_decay": 0.9995,
        "pretrained": "torchvision ImageNet-1K DEFAULT",
        "torch": torch.__version__,
        "torchvision": __import__("torchvision").__version__,
        "device": torch.cuda.get_device_name(0),
    }
    (args.output / "experiment_identity.json").write_text(
        json.dumps(
            {"config": config, "source": source_identity, "prep": prep_report},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    for epoch in range(1, args.epochs + 1):
        train_dataset.epoch = epoch
        model.train()
        loss_total = 0.0
        samples = 0
        epoch_started = time.monotonic()
        for images, labels in train_loader:
            images = images.to(
                device, non_blocking=True, memory_format=torch.channels_last
            )
            labels = labels.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type="cuda", dtype=torch.float16):
                logits = model(images)
                loss = criterion(logits, labels)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            scheduler.step()
            ema.update_parameters(model)
            batch_samples = labels.numel()
            samples += batch_samples
            loss_total += float(loss.detach()) * batch_samples
        metrics = evaluate(
            ema.module, dev_loader, device=device, classes=len(characters)
        )
        row = {
            "epoch": epoch,
            "train_samples": samples,
            "train_loss": loss_total / samples,
            "learning_rate": optimizer.param_groups[0]["lr"],
            "development": metrics,
            "epoch_seconds": time.monotonic() - epoch_started,
            "elapsed_seconds": time.monotonic() - started,
        }
        with log_path.open("a", encoding="utf-8", newline="\n") as log:
            log.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)
        checkpoint = {
            "schema_version": 1,
            "config": config,
            "characters": characters,
            "epoch": epoch,
            "development": metrics,
            "model_state": model.state_dict(),
            "ema_state": ema.module.state_dict(),
        }
        atomic_torch_save(checkpoint, args.output / "last.pt")
        key = (float(metrics["top1"]), float(metrics["macro_top1"]))
        if key > best_key:
            best_key = key
            atomic_torch_save(checkpoint, args.output / "best.pt")

    best_path = args.output / "best.pt"
    summary = {
        "state": "completed",
        "best_checkpoint_sha256": sha256_file(best_path),
        "best_checkpoint_bytes": best_path.stat().st_size,
        "best_development": torch.load(
            best_path, map_location="cpu", weights_only=False
        )["development"],
        "elapsed_seconds": time.monotonic() - started,
    }
    (args.output / "result.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
