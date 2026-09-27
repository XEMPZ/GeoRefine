"""文件坐标/高程转换后端：DXF(app.cad)、SHP、文本点文件、南方 ipj 方案导入。

坐标系约定（与各模块文档一致）：
- DXF/SHP 内部 (X=东, Y=北)；转换回调 fn_cad(X_east, Y_north[, z])
- 测量系 (x=北, y=东)；文本点文件回调 fn_survey(x_north, y_east[, z])
"""
