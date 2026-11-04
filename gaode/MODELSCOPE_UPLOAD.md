# 魔搭赛题怎么提测（对照平台弹窗）

报名仓库灰色锁住 `https://github.com/AZLyyds/seekdb-contest` 是正常的，**不用改**。

## 一步步做

### 1. 点「获取验证行」
- 在成绩提测弹窗点 **获取验证行**
- 复制整行（一般是 `OB-P:...` 这种，**整行原样**，多空格都不要改）
- 同一队本题通常共用一条，重复点获取还是同一条

### 2. 上传 Skill 到魔搭
1. 打开 https://www.modelscope.cn/skills （登录你的魔搭账号）
2. 走「上传 / 创建 Skill」流程
3. 用已打好的包（二选一）：
   - 网页上传：把 zip 拖进去  
     `垃圾/oceanbase-miniob/powercontext-contest/modelscope_skill_pack/nearby-places-explorer.zip`
   - 或新建 Skill，内容用  
     `.../modelscope_skill_pack/nearby-places-explorer/SKILL.md`
4. 发布成功后，打开 Skill **公开页面**，地址类似：  
   `https://modelscope.cn/skills/你的用户名/nearby-places-explorer`  
   （以页面实际 URL 为准）

### 3. 把验证行写进 Skill
平台要靠验证行认领「这是你队的作品」：
- 编辑刚上传的 Skill 的 `SKILL.md`
- **在文件末尾单独一行**，粘贴第 1 步的验证行（不要加引号、不要改字）
- 保存 / 重新发布

若网页不好改：本地改 `SKILL.md` 末尾加上验证行 → 重新打 zip → 更新 Skill。

### 4. 回大赛弹窗提交
- **作品链接**：填第 2 步那个 `https://modelscope.cn/skills/...` 完整 URL  
- 点 **提交**（填完链接后按钮才会亮）

## 注意
- 不要填个人主页、账号 ID、只填 Skill 名
- 无痕窗口打开作品链接，确认能看到内容且末尾有验证行
- 高德题和魔搭题是分开的；魔搭只认魔搭链接 + 验证行
