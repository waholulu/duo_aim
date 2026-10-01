"""Render measured YOLO box/nose coordinates over the corresponding saved frame."""
from pathlib import Path
import cv2,json
for name in ['full640','fast240-full','fp16-full640']:
 p=Path(f'results/{name}-best.json')
 if not p.exists():continue
 r=json.loads(p.read_text());im=cv2.imread(f'results/{name}-best.png')
 x1,y1,x2,y2=map(round,r['bbox_xyxy']);x,y=map(round,r['head_xy'])
 cv2.rectangle(im,(x1,y1),(x2,y2),(0,255,0),3)
 cv2.drawMarker(im,(x,y),(0,0,255),cv2.MARKER_CROSS,24,3)
 cv2.drawMarker(im,(im.shape[1]//2,im.shape[0]//2),(255,255,0),cv2.MARKER_CROSS,35,2)
 label=f'Nose ({x}, {y}) conf={r["head_confidence"]:.2f} | {r["callback_to_coords_ms"]:.2f} ms'
 cv2.putText(im,label,(25,im.shape[0]-35),cv2.FONT_HERSHEY_SIMPLEX,0.85,(255,255,255),2)
 cv2.imwrite(f'results/{name}-annotated.jpg',im)
