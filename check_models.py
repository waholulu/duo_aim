from ultralytics import YOLO
import cv2
from crop_utils import center_crop
im=cv2.imread('results/capture.png'); crop=center_crop(im,960)
for name in ['yolo26n-pose.pt','yolo11s-pose.pt']:
 m=YOLO(name)
 for s in [320,640]:
  r=m(crop,imgsz=s,verbose=False,device='cpu')[0]
  print(name,s,r.boxes.xyxy.tolist(),r.boxes.conf.tolist(),r.keypoints.data[:,:5].tolist(),flush=True)
  cv2.imwrite('results/'+name+f'-{s}.jpg',r.plot())
