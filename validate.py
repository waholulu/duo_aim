from ultralytics import YOLO
import cv2,json
im=cv2.imread('results/capture.png')
h,w=im.shape[:2]; side=960;x=(w-side)//2;y=(h-side)//2
m=YOLO('yolo11n-pose.pt')
for s in (320,640):
 r=m(im[y:y+side,x:x+side],imgsz=s,device='cpu',verbose=False)[0]
 print(s,r.boxes.xyxy.tolist(),r.boxes.conf.tolist(),r.keypoints.data[:,:5].tolist(),flush=True)
 cv2.imwrite(f'results/validate-{s}.jpg',r.plot())
