import json
import difflib
from collections import Counter
import glob
import os

# Mapping
mapping = {
    "mth1000-006": ("library/evaluations/runs/toolbelt5-mthv2-dev24-c1/cases/case-5aeb7f7edd6dd259/attempts/attempt-0001/challenger/mthv2_mth1000_006/runs/transcribe_omp_toolbelt5/c0f890f2fd6b0581/out/transcription.json",
                    "library/evaluations/runs/toolbelt5-mthv2-dev24-c1/cases/case-5aeb7f7edd6dd259/attempts/attempt-0001/challenger/mthv2_mth1000_006/runs/transcribe_omp_toolbelt5/c0f890f2fd6b0581/evidence/geometry.json",
                    "palimpsest/factory/evaluation/gold/transcribe/mthv2-development/mth1000-006.json"),
    "mth1200-GL-1054-1-12": ("library/evaluations/runs/toolbelt5-mthv2-dev24-c3/cases/case-14ec6f0bb7c0e9d9/attempts/attempt-0001/challenger/mthv2_mth1200_gl_1054_1_12/runs/transcribe_omp_toolbelt5/9a2c4122245fda09/out/transcription.json",
                           "library/evaluations/runs/toolbelt5-mthv2-dev24-c3/cases/case-14ec6f0bb7c0e9d9/attempts/attempt-0001/challenger/mthv2_mth1200_gl_1054_1_12/runs/transcribe_omp_toolbelt5/9a2c4122245fda09/evidence/geometry.json",
                           "palimpsest/factory/evaluation/gold/transcribe/mthv2-development/mth1200-GL-1054-1-12.json"),
    "mth1200-GL-1054-1-13": ("library/evaluations/runs/toolbelt5-mthv2-dev24-c4/cases/case-15f1cb52f013b95e/attempts/attempt-0001/challenger/mthv2_mth1200_gl_1054_1_13/runs/transcribe_omp_toolbelt5/b140068f9d93b8bb/out/transcription.json",
                           "library/evaluations/runs/toolbelt5-mthv2-dev24-c4/cases/case-15f1cb52f013b95e/attempts/attempt-0001/challenger/mthv2_mth1200_gl_1054_1_13/runs/transcribe_omp_toolbelt5/b140068f9d93b8bb/evidence/geometry.json",
                           "palimpsest/factory/evaluation/gold/transcribe/mthv2-development/mth1200-GL-1054-1-13.json"),
    "tkh-0001-001-26-15": ("library/evaluations/runs/toolbelt5-mthv2-dev24-c1/cases/case-69800122fe3b8cfd/attempts/attempt-0001/challenger/mthv2_tkh_0001_001_26_15/runs/transcribe_omp_toolbelt5/1b996dfeb65915dd/out/transcription.json",
                          "library/evaluations/runs/toolbelt5-mthv2-dev24-c1/cases/case-69800122fe3b8cfd/attempts/attempt-0001/challenger/mthv2_tkh_0001_001_26_15/runs/transcribe_omp_toolbelt5/1b996dfeb65915dd/evidence/geometry.json",
                          "palimpsest/factory/evaluation/gold/transcribe/mthv2-development/tkh-0001-001-26-15.json")
}

def get_no_space(s):
    return "".join(s.split())

def bag_recall(s1, s2):
    c1 = Counter(get_no_space(s1))
    c2 = Counter(get_no_space(s2))
    intersection = sum((c1 & c2).values())
    return intersection / len(get_no_space(s2)) if len(get_no_space(s2)) > 0 else 1.0

def sequence_recall(s1, s2):
    s1_ns = get_no_space(s1)
    s2_ns = get_no_space(s2)
    matcher = difflib.SequenceMatcher(None, s1_ns, s2_ns)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / len(s2_ns) if len(s2_ns) > 0 else 1.0

results = {}

for doc_id, (trans_p, geom_p, gold_p) in mapping.items():
    with open(trans_p, encoding='utf-8') as f: trans = json.load(f)["transcription"]
    with open(gold_p, encoding='utf-8') as f: gold = json.load(f)["text"]
    with open(geom_p, encoding='utf-8') as f: geom = json.load(f)
    
    reader_segments = []
    if "columns" in geom:
        for col in geom["columns"]:
            if "second_reader" in col and col["second_reader"]:
                reader_segments.append(col["second_reader"])
    reader_concat = "".join(reader_segments)
    
    res = {
        "agent_vs_gold_seq": sequence_recall(trans, gold),
        "reader_vs_gold_seq": sequence_recall(reader_concat, gold),
        "reader_vs_agent_seq": sequence_recall(reader_concat, trans),
    }
    
    if "GL-1054" in doc_id:
        res["agent_vs_gold_bag"] = bag_recall(trans, gold)
        res["reader_vs_gold_bag"] = bag_recall(reader_concat, gold)
    
    results[doc_id] = res

print(json.dumps(results, indent=2))
