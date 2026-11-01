"""Actual unmodified Three upload JPEG plus independent negative pixels."""
from _offline_guard import require_offline_copy
require_offline_copy()
import hashlib
from pathlib import Path
import unittest
import cv2 as cv
import numpy as np
from backend.core.vision_workspace import VisionWorkspace,settings

FIXTURE=Path(__file__).parent/'fixtures/vision/three-circle-upload-640x480.jpg'
CONFIG=settings({'input':'virtual','enabled':True,'detection_enabled':True,'mapping':False,'height_mm':24,'min_area':100,'roi':[0,0,1,1],'colors':['red']})

def classify(image):
    engine=VisionWorkspace(None)
    output=engine.analyze(image,CONFIG,None,'virtual:pixel-regression:fixed-view')
    values=[v for v in output['objects'] if v['color']=='red']
    if len(values)!=1:raise AssertionError('Expected one independently rendered red region: '+str(values))
    return values[0]

def encoded(draw,quality=80):
    image=np.full((240,320,3),120,dtype=np.uint8);draw(image)
    ok,data=cv.imencode('.jpg',image,[cv.IMWRITE_JPEG_QUALITY,quality]);assert ok
    return data.tobytes()

class Pixels(unittest.TestCase):
    def test_actual_original_upload_jpeg_circle_is_not_irregular(self):
        data=FIXTURE.read_bytes()
        # Original fetch body.image_base64, not the backend's recompressed preview.
        # Source: output/renewal/source-sorting-diagnostic-v4/result.json.
        self.assertEqual(hashlib.sha256(data).hexdigest(),'7549360cdc690e791f24a1ee49ba49c2d01629466466a1840f0575d6292fa371')
        value=classify(data)
        self.assertEqual(value['shape'],'circle')
        self.assertEqual(value['color'],'red');self.assertEqual(value['center_px'],[319.5,239.5])
        self.assertEqual(value['area_px'],4372.)
        self.assertEqual(value['corners'],[[357.,277.],[282.,277.],[282.,202.],[357.,202.]])
        self.assertIsNone(value['mapped'])

    def test_ideal_and_compressed_circles_of_actual_target_sizes(self):
        for radius in (22,38):
            for quality in (55,80,95):
                with self.subTest(radius=radius,quality=quality):
                    value=classify(encoded(lambda im:cv.circle(im,(160,120),radius,(130,160,255),-1,lineType=cv.LINE_AA),quality))
                    self.assertEqual(value['shape'],'circle')

    def test_rectangles_triangles_and_actual_irregular_pixels_do_not_become_circles(self):
        draws={
            'rectangle':lambda im:cv.rectangle(im,(120,80),(200,160),(130,160,255),-1),
            'rotated_rectangle':lambda im:cv.fillPoly(im,[cv.boxPoints(((160,120),(90,60),27)).astype(np.int32)],(130,160,255)),
            'triangle':lambda im:cv.fillPoly(im,[np.array([[160,70],[110,160],[210,160]],np.int32)],(130,160,255)),
            'notched':lambda im:(cv.circle(im,(160,120),40,(130,160,255),-1),cv.rectangle(im,(160,100),(220,140),(120,120,120),-1)),
            'elongated':lambda im:cv.ellipse(im,(160,120),(55,25),0,0,360,(130,160,255),-1),
            'l_shape':lambda im:cv.fillPoly(im,[np.array([[120,80],[160,80],[160,120],[200,120],[200,160],[120,160]],np.int32)],(130,160,255)),
            'star':lambda im:cv.fillPoly(im,[np.array([[160,70],[171,102],[205,102],[178,125],[188,160],[160,140],[132,160],[142,125],[115,102],[149,102]],np.int32)],(130,160,255)),
        }
        for name,draw in draws.items():
            for quality in (55,80,95):
                with self.subTest(name=name,quality=quality):
                    value=classify(encoded(draw,quality))
                    if name in ('rectangle','rotated_rectangle','triangle'):self.assertEqual(value['shape'],'rectangle' if name=='rotated_rectangle' else name)
                    else:self.assertEqual(value['shape'],'irregular')

if __name__=='__main__':unittest.main()
