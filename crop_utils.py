"""Use the same center crop bounds as the Swift detectors."""

def center_crop(image, size):
    height, width = image.shape[:2]
    crop_width = min(size, width) if size > 0 else width
    crop_height = min(size, height) if size > 0 else height
    x = (width - crop_width) // 2
    y = (height - crop_height) // 2
    return image[y:y + crop_height, x:x + crop_width]
