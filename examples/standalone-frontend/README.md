# 独立前端示例

这是一个纯 HTML/CSS/JavaScript 参考客户端。它不加载 `web/`、`web-dock/` 的代码，不导入 Python，不访问 SQLite；所有项目、资产、素材、上传和实时通知都通过 `/api/v1` 完成。

## 运行

在仓库根目录执行：

```powershell
python -m http.server 5173 --directory examples/standalone-frontend
```

浏览器打开 `http://localhost:5173`，在 API 地址输入框填入 `http://NAS地址:8092`。如果前端和 API 不同源，需要把浏览器显示的精确 Origin 加到后端 `TEAM_ASSETS_CORS_ORIGINS`；例如 `http://localhost:5173`。Origin 配置是浏览器跨域边界，不是用户认证。共享服务只能在可信内网/VPN 中使用，不能暴露到公网。

本示例覆盖：

- 查询项目和项目资产。
- 查看资产、变体、素材缩略图，点击查看原图。
- 创建资产、修改名称/分类/定位说明。
- 通过公开 multipart API 上传图片。
- 监听 SSE revision，其他前端修改后自动刷新。
- 发生 409 时展示服务端当前版本，提醒检查后再提交。

用两个浏览器窗口打开此示例，或一个窗口打开官方前端，即可检查两端是否通过同一数据服务同步。跨来源的最终浏览器验收应在实际 NAS/反向代理配置上另行执行。
