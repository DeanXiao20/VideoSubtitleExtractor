# 在线发布指南 — Cloudflare Pages

将本地生成的双语字幕 HTML 发布到 Cloudflare Pages，通过 Cloudflare Access 保护为私有访问，随时随地手机/电脑查看。

---

## 1. 前置条件

- Cloudflare 账号（免费）：https://dash.cloudflare.com/sign-up
- Node.js 18+（安装 wrangler CLI）
- 本项目已配置 `.env` 中的 `LLM_API_KEY`

## 2. 安装 Wrangler CLI

```bash
npm install -g wrangler
```

登录 Cloudflare：

```bash
wrangler login
```

浏览器会打开授权页面，点击 Allow 完成授权。

## 3. 创建 Pages 项目

```bash
wrangler pages project create my-subtitles
```

- 项目名自定义，例如 `my-subtitles`
- 生产域名将是 `https://my-subtitles.pages.dev`
- 也可以后续绑定自定义域名

## 4. 配置项目

编辑项目根目录 `.env` 文件，添加：

```env
CF_PAGES_PROJECT=my-subtitles
```

## 5. 发布

```bash
python src/publish.py --target cloudflare
```

脚本会自动：
1. 从数据库读取所有视频
2. 重新生成全部 HTML（含可读文件名 + 列表页 `index.html`）
3. 调用 wrangler 部署到 Cloudflare Pages

发布成功后输出类似：

```
✨ Successfully published your site to:
https://my-subtitles.pages.dev
```

## 6. 配置 Cloudflare Access（私有访问）

发布后网站是公开的，需要配置 Access 添加身份认证。

### 6.1 进入 Zero Trust 控制台

1. 登录 https://one.dash.cloudflare.com
2. 首次使用需设置团队名称（如 `my-team`）
3. 选择免费计划（Free，最多 50 用户）

### 6.2 添加 Access Application

1. 左侧菜单 → **Access** → **Applications**
2. 点击 **Add an application**
3. 选择 **Self-hosted**
4. 配置：

| 字段 | 值 |
|------|---|
| Application name | `双语字幕文档库` |
| Session Duration | `24 hours` |
| Application domain | `my-subtitles.pages.dev` |

5. 点击 **Next**

### 6.3 配置访问策略

1. Policy name: `仅自己访问`
2. Action: **Allow**
3. Include 规则选择 **Emails**，填入你的邮箱地址
4. 点击 **Next** → **Add application**

### 6.4 配置登录方式

1. 左侧菜单 → **Access** → **Authentication**
2. 点击 **Add an identity provider**
3. 选择 **One-time PIN**（邮箱验证码登录，免费）
4. 也可添加 Google / GitHub 等社交登录

## 7. 访问

### 首次访问

1. 打开 `https://my-subtitles.pages.dev`
2. 自动跳转到 Cloudflare Access 登录页
3. 输入你配置的邮箱
4. 收到验证码邮件，输入验证码
5. 登录成功，进入字幕文档列表页

### 后续访问

- 24 小时内无需重新登录（可在 Access 设置中调整 Session Duration）
- 手机浏览器同样可以访问

### 列表页

访问 `https://my-subtitles.pages.dev/` 即可看到所有已发布文档的列表，点击标题或"查看"按钮打开对应的双语字幕 HTML。

## 8. 更新发布

每次在本地处理新视频后，重新执行：

```bash
python src/publish.py --target cloudflare
```

新增/修改的 HTML 会自动更新到线上。

## 9. 可选：绑定自定义域名

如果你有自己的域名且 DNS 托管在 Cloudflare：

1. Cloudflare Dashboard → Pages → `my-subtitles` → **Custom domains**
2. 点击 **Set up a custom domain**
3. 输入如 `subtitles.yourdomain.com`
4. 按提示添加 CNAME 记录
5. Access 保护会自动应用到自定义域名

## 10. 可选：GitHub Pages 备选方案

如果不想使用 Cloudflare，也可以推送到 GitHub：

```bash
# 先创建一个 GitHub 仓库，然后在 .env 中配置：
GITHUB_PAGES_REPO=https://github.com/username/subtitles-pages.git

# 发布
python src/publish.py --target github
```

> **注意**：GitHub Pages 免费版必须公开仓库，无法实现私有访问。如需私有，推荐使用 Cloudflare Pages + Access。

---

## 常见问题

**Q: 发布时报错 `wrangler not found`**
A: 确认 `npm install -g wrangler` 已执行，且 Node.js 18+ 已安装。

**Q: 访问时提示 Access Denied**
A: 检查 Access 策略中你的邮箱是否在允许列表中。

**Q: 收不到验证码邮件**
A: 检查垃圾邮件文件夹。也可在 Access → Authentication 中启用 Google/GitHub 登录替代。

**Q: 手机访问排版是否正常**
A: HTML 已做移动端响应式适配，手机浏览器可正常查看。

**Q: 免费额度够用吗**
A: Cloudflare Pages 免费版无限带宽、无限请求。Access 免费版支持 50 用户。个人使用完全足够。
