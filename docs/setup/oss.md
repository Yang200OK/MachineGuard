# 私有 OSS 配置

原文与报告导出存储在私有 OSS。请使用自己的私有 Bucket 和专用 RAM 身份；实际资源配置与凭据不随源码提交。

## 配置文件

首次运行从模板创建实际配置；已有文件不覆盖。

```powershell
Copy-Item -LiteralPath '.\configs\oss.example.json' -Destination '.\configs\oss.json'
Copy-Item -LiteralPath '.\configs\oss-ram-policy.example.json' -Destination '.\configs\oss-ram-policy.json'
```

在 `oss.json` 填写 Bucket、地域与对应 Endpoint；保留应用目录前缀。在实际策略中将 `YOUR_BUCKET_NAME` 替换为自己的 Bucket，并给专用 RAM 身份绑定该策略。

策略限定 Bucket 信息读取和应用前缀内的对象写入、读取、访问控制查询；删除仅开放给验证对象目录。不要直接给应用授予整个账号的 OSS 管理权限。Bucket 保持私有，匿名读取检查应返回 403。

## 凭据

将专用 RAM 身份的 AccessKey 保存为 Windows 用户环境变量 `AccessKey_ID` 和 `AccessKey_Secret`，在新开的 API、Worker 终端加载：

```powershell
$env:AccessKey_ID = [Environment]::GetEnvironmentVariable('AccessKey_ID', 'User')
$env:AccessKey_Secret = [Environment]::GetEnvironmentVariable('AccessKey_Secret', 'User')
```

不将实际密钥放入配置模板、前端变量、源码或截图。根目录 `.env` 仅填写 `.env.example` 声明的服务设置。实际 OSS 配置文件已被 Git 忽略。

## 实际检查

```powershell
& .\.venv-api\Scripts\python.exe -X utf8 .\scripts\check_oss_identity.py
& .\.venv-api\Scripts\python.exe -X utf8 .\scripts\check_oss.py
```

身份检查核对实际调用者。存储检查会在验证目录上传临时对象、读取并核对摘要、检查匿名拒绝，最后清理该验证对象；失败明确报告，不输出密钥。执行前确认当前环境变量属于正确的 RAM 身份。

这些检查验证身份与存储连通性，不能替代“API 创建导出任务 → Worker 渲染与上传 → 授权下载”的完整验证。报告本地渲染已检查，真实自动化导出全链路验证仍有缺口。
