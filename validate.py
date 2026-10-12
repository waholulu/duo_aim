"""Run reference PyTorch pose inference on an explicit saved capture."""
import argparse
from pathlib import Path


def run(models, image_path, sizes, crop_size, output_dir):
    import cv2
    from ultralytics import YOLO
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f'Cannot read {image_path}')
    h, w = image.shape[:2]
    cw, ch = (min(crop_size, w), min(crop_size, h)) if crop_size else (w, h)
    x, y = (w-cw)//2, (h-ch)//2
    crop = image[y:y+ch, x:x+cw]
    output_dir.mkdir(parents=True, exist_ok=True)
    for path in models:
        model = YOLO(str(path))
        for size in sizes:
            result = model(crop, imgsz=size, device='cpu', verbose=False)[0]
            print(path, size, result.boxes.xyxy.tolist(), result.boxes.conf.tolist(), result.keypoints.data[:, :5].tolist(), flush=True)
            destination = output_dir / f'{Path(path).stem}-{size}.jpg'
            if not cv2.imwrite(str(destination), result.plot()):
                raise OSError(f'Cannot write {destination}')


def main(default_models=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--models', nargs='+', default=default_models or ['yolo11n-pose.pt'])
    parser.add_argument('--sizes', type=int, nargs='+', default=[320, 640])
    parser.add_argument('--crop', type=int, default=960)
    parser.add_argument('--output-dir', type=Path, default=Path('results/reference'))
    args = parser.parse_args()
    if args.crop < 0 or any(size <= 0 for size in args.sizes):
        parser.error('crop must be nonnegative and sizes positive')
    run(args.models, args.image, args.sizes, args.crop, args.output_dir)


if __name__ == '__main__':
    main()
