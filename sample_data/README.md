# 样例数据说明

| 文件 | 内容 | 真值/公式 |
|---|---|---|
| 控制点样本_平面对.csv | 12 点平面控制点对 | dx=+102.3456, dy=−56.7890, scale=0.9999975, rot=+0.25″；dh=平面(中心=末点)；噪声平面5mm/高程8mm（种子42） |
| 控制点样本_空间对.csv | 10 点空间控制点对 | dx=+1.234, dy=−2.345, dz=+3.456 m; rx=+0.30″, ry=−0.20″, rz=+0.15″, scale=+2.5ppm（种子43） |
| 样例测区.dxf | 9 类实体（POINT/TEXT/LWPOLYLINE/POLYLINE3D/CIRCLE/ARC/INSERT/MTEXT/LINE），4 图层 | 坐标为源系（CAD X东,Y北） |
| 样例照片POS/ | 8 张 DJI 风格 JPG（EXIF GPS+XMP），嵌套 A/B 子目录 | 椭球高 35~60m |
| 局部似大地水准面格网_样例.csv | 21×21 规则格网 0.01° | ξ = 12.0 + 0.0008·(lon−114)·100 + 0.0005·(lat−30)·100 |
| 精度对比样本.csv | 15 点 lon/lat/H_ell/H_normal | H_normal = H_ell − ξ + N(0,0.02)（种子7） |

重新生成: `python tools/make_sample_data.py`
