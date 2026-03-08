"""
Simple threshold tool for manuscript cleanup.
Drag sliders to find the optimal black/white cutoff to eliminate bleed-through.
"""
import cv2
import numpy as np
import sys

def nothing(x):
    pass

def main(image_path):
    # Load image
    img = cv2.imread(image_path)
    if img is None:
        print(f"Could not load image: {image_path}")
        return

    # Resize for display if too large
    max_height = 900
    h, w = img.shape[:2]
    if h > max_height:
        scale = max_height / h
        img = cv2.resize(img, (int(w * scale), int(h * scale)))

    # Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Create window
    cv2.namedWindow('Threshold Tool', cv2.WINDOW_NORMAL)

    # Create trackbars
    cv2.createTrackbar('Threshold', 'Threshold Tool', 128, 255, nothing)
    cv2.createTrackbar('Mode', 'Threshold Tool', 0, 1, nothing)  # 0=binary, 1=adaptive
    cv2.createTrackbar('Block Size', 'Threshold Tool', 11, 99, nothing)  # For adaptive
    cv2.createTrackbar('C Offset', 'Threshold Tool', 2, 50, nothing)  # For adaptive
    cv2.createTrackbar('Invert', 'Threshold Tool', 0, 1, nothing)

    print("Controls:")
    print("  Threshold: Global threshold value (for Mode=0)")
    print("  Mode: 0=Global, 1=Adaptive")
    print("  Block Size: Neighborhood size for adaptive (must be odd)")
    print("  C Offset: Constant subtracted from mean (adaptive)")
    print("  Invert: Flip black/white")
    print("  Press 's' to save, 'q' to quit")

    while True:
        # Get trackbar values
        thresh_val = cv2.getTrackbarPos('Threshold', 'Threshold Tool')
        mode = cv2.getTrackbarPos('Mode', 'Threshold Tool')
        block_size = cv2.getTrackbarPos('Block Size', 'Threshold Tool')
        c_offset = cv2.getTrackbarPos('C Offset', 'Threshold Tool')
        invert = cv2.getTrackbarPos('Invert', 'Threshold Tool')

        # Ensure block_size is odd and >= 3
        if block_size < 3:
            block_size = 3
        if block_size % 2 == 0:
            block_size += 1

        # Apply threshold
        if mode == 0:
            # Global threshold
            thresh_type = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
            _, result = cv2.threshold(gray, thresh_val, 255, thresh_type)
        else:
            # Adaptive threshold
            thresh_type = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
            result = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                thresh_type, block_size, c_offset
            )

        # Show result
        cv2.imshow('Threshold Tool', result)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('s'):
            out_path = image_path.replace('.jpg', '_threshold.png')
            cv2.imwrite(out_path, result)
            print(f"Saved to: {out_path}")

    cv2.destroyAllWindows()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        # Default to f001r
        image_path = r"D:\Projects\palimpsest\library\vat_reg_lat_1515\images\f001r.jpg"
    else:
        image_path = sys.argv[1]

    main(image_path)
