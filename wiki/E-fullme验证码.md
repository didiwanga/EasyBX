# E-fullme 验证码窗口（重设计）

> 不做 OCR（用户明确：自动识别率低到鸡肋，完全不做）。
> 重设计「输入 + 发送 fullme」方案；窗口弹出时机与处理沿用旧版已达预期部分（参考但不照抄）。

## 一、窗口构成（继承旧版已达预期部分）
- **弹窗时机/页面**：检测到 fullme 图链后，弹出 4 分网格验证码窗口 + 单图窗口（FullmeWindow）/ 宏验证码窗口（CaptchaWindow）。
- **显示**：**不依赖 QtWebEngine** — QNetworkAccessManager 下载图片（QLabel 显示）；若地址是 HTML 页则解析其中 `<img src>` 再下载真图。
- **直连**：fullme 服务器公网直连，`setProxy(NoProxy)` 不走系统代理（VPN/加速器/企业代理会导致 DNS 偶尔失败）。
- **传输超时/重试**：10s 传输超时；DNS/连接失败/超时等偶发网络错误自动重试 2 次（禁缓存、每次真实请求）。
- **置顶**：默认置顶 checkbox（旧版已达预期）。

## 二、【重点】输入行是否显示的判断 —— 满两条来源
用户要求：**判断验证码来源**，决定是否显示底部输入行+发送按钮。

- **用户手动 fullme** → 显示输入行+发送按钮（用户要填码）。
- **任务线产生的验证码** → 隐藏输入行（不填码，只看图）。

实现：fullme_detector 在监测到验证码时，标注 `source`：
```python
source = 'manual'   # 用户发了 fullme/手动 look 触发
       | 'task'     # 任务线（npc 对话/买卖/任务触发的验证码）
```

### 来源判定设计（简版，可配置阈值）
- 检测「用户本次命令是否是 fullme」：记录最近一条 `send_command`，若为 `fullme`/`fullme `<则标记 manual。
- 否则任务线产生 → 标记 task。
- 若无法判断 → 默认显示输入行（保守，避免该填码时没输入框）。

> 注：当前实现满窗（FullmeGridWindow/FullmeWindow）均显示输入行，来源细分（task 隐藏输入行）留给后续按此文档补充。

## 三、【新设计】输入行交互
- 位置：窗口**底部内嵌一行** `QLineEdit + 发送按钮`（不弹独立模态框）。
- **只填验证码原文**，发送时自动拼 `fullme <码>` 并回车。
- 回车即发送（returnPressed）。
- 可取消/重置输入。

## 四、发送后处理（等待回话模式，fullme 窗口）
- 发送 `fullme <码>` 后**不立即关窗**，监听服务器回话：
  - 成功 `你突然感到精神一振…` → 自动关闭窗口。
  - 失败 `好像什么都没有发生…` → 提示「输入的验证码可能有误」，清空等待重输。
  - 最大 3 次（1 次首发 + 2 次错误重输），用尽关闭。
  - **超时兜底**：发送后 180s 无任何回话 → 按失败处理提示重输。
- 宏验证码窗口（CaptchaWindow）**不启用**该模式：提交即把验证码回传回调（宏赋值变量）并关窗。

## 五、红包口令窗口（HongbaoWindow）
- **检测**：服务器消息含 `robot.php?filename=<口令>` **且含红包语义词**（`红包`/`hongbao`/`抢红包`），形如「在线发出红包，请到 http://fullme.pkuxkx.net/robot.php?filename=xxx 查询口令。抢红包命令 hongbao <口令>」。
- **与 fullme 区分**：fullme 链接也可能走 `robot.php?filename=`，故红包判定须同时命中 URL 与红包语义词；无红包词的链接一律按 fullme 处理。
- **弹窗**：同一消息内红包链接优先识别，发布 `hongbao.detected`（account, url），弹出红包口令窗口；该链接不再当作 fullme 处理。
- **交互**：图片加载同 fullme（NoProxy + 10s 超时 + 重试 2 次）；底部输入框只填口令，回车即发送 `hongbao <口令>` 并关窗（不启用等待回话模式）。
- **入口**：`core/fullme.py::extract_hongbao_url` → `session._maybe_fullme` → `mainwindow._on_hongbao` → `ui/fullme.py::HongbaoWindow`。

## 六、结构
```
[4宫格验证码图]
───────────────
[输入框：只填码] [发送]     ← source=manual 时显示；source=task 时隐藏
```
- task 窗口仅显示图，顶部保留「输入」备用（小按钮可显示输入行）。

## 七、验收
- [ ] 不落地 OCR（无任何识别）
- [ ] 无 QWebEngine 可显示图（HTTP 下载 + `<img>` 解析降级）
- [ ] 直连 NoProxy；10s 传输超时 + 偶发失败自动重试 2 次
- [ ] 只填码自动拼 fullme + 回车
- [ ] 成功自动关窗；失败提示重输（最多 3 次）；超时 180s 按失败处理
- [ ] 宏验证码窗口提交即关窗并回传验证码
- [ ] 红包链接弹出 HongbaoWindow，提交发送 `hongbao <口令>` 并关窗
- [ ] manual 窗口显示底部输入行；task 来源细分显示（待补）
- [ ] 窗口弹出/处理沿用预期