from ultralytics import YOLO
import cv2,json
from crop_utils import center_crop
im=cv2.imread('results/capture.png')
crop=center_crop(im,960)
m=YOLO('yolo11n-pose.pt')
for s in (320,640):
 r=m(crop,imgsz=s,device='cpu',verbose=False)[0]
 print(s,r.boxes.xyxy.tolist(),r.boxes.conf.tolist(),r.keypoints.data[:,:5].tolist(),flush=True)
 cv2.imwrite(f'results/validate-{s}.jpg',r.plot())
