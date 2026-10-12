"""Overlay a saved benchmark or detector diagnostic frame using explicit paths."""
import argparse
import json
from pathlib import Path


def main():
    import cv2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('metadata', type=Path)
    parser.add_argument('--image', type=Path, help='Default: adjacent PNG (also supports .png.json diagnostics)')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    row = json.loads(args.metadata.read_text())
    image_path = args.image or (args.metadata.with_suffix('') if args.metadata.name.endswith('.png.json') else args.metadata.with_suffix('.png'))
    image = cv2.imread(str(image_path))
    if image is None:
        parser.error(f'Cannot read {image_path}')
    detections = row.get('detections', [row] if 'head_xy' in row else [])
    for detection in detections:
        x1, y1, x2, y2 = map(round, detection['bbox_xyxy'])
        x, y = map(round, detection['head_xy'])
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 3)
        cv2.drawMarker(image, (x, y), (0, 0, 255), cv2.MARKER_CROSS, 24, 3)
    cv2.drawMarker(image, (image.shape[1]//2, image.shape[0]//2), (255, 255, 0), cv2.MARKER_CROSS, 35, 2)
    destination = args.output or image_path.with_name(image_path.stem+'-annotated.jpg')
    if not cv2.imwrite(str(destination), image):
        raise OSError(f'Cannot write {destination}')
    print(destination)


if __name__ == '__main__':
    main()
