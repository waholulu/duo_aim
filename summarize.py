"""Summarize actual capture/inference timing; excludes invalid prototype results."""
import json
from pathlib import Path
import numpy as np
names=['crop320-fixed','full640','crop320-gpu','crop320-ane','fast240','fast240-full','fast240-repeat','fp16-crop320','fp16-full640']
summary={}
for name in names:
 p=Path(f'results/{name}.json')
 if not p.exists():continue
 d=json.loads(p.read_text()); rows=d['rows']
 if not rows:continue
 valid=[r for r in rows if r['head_valid']]
 s={'samples':len(rows),'valid_heads':len(valid),'model_size':d['model_size'],'crop':d['crop'],'compute_units':d['compute_units'],'dropped_callbacks':d['dropped_callbacks']}
 for key in ['preprocess_ms','inference_ms','postprocess_ms','callback_to_coords_ms','pts_to_callback_ms','pts_to_coords_ms']:
  s[key]=dict(zip(['min','median','p95','max'],map(float,np.percentile([r[key] for r in rows],[0,50,95,100]))))
 if valid:
  s['fastest_valid_head']=min(valid,key=lambda r:r['callback_to_coords_ms'])
 t=[r['host_start_s'] for r in rows]
 s['processed_fps']=(len(t)-1)/(t[-1]-t[0])
 pts=np.array([r['pts_s'] for r in rows]);s['pts_interval_median_ms']=float(np.median(np.diff(pts))*1000)
 summary[name]=s
Path('results/summary.json').write_text(json.dumps(summary,indent=2))
for name,s in summary.items():
 print(name,'n=',s['samples'],'heads=',s['valid_heads'],'fps=',round(s['processed_fps'],2),'processing=',s['callback_to_coords_ms'],'pts_to_coords=',s['pts_to_coords_ms'])
