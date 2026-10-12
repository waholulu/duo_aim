"""Compare reference pose models on an explicit saved image."""
from validate import main

if __name__ == '__main__':
    main(['yolo26n-pose.pt', 'yolo11s-pose.pt'])
