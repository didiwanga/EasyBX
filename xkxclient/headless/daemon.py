from __future__ import annotations

import json
import traceback
from collections import deque
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PyQt6.QtCore import QObject, QTimer

from xkxclient.app import XkxApp
from xkxclient.core.config import ConfigManager, json_write
from xkxclient.headless.control import ControlServer, QtBridge

_INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>EasyBXb 无头控制台</title>
<style>
  body{font-family:system-ui,Segoe UI,Microsoft YaHei,sans-serif;margin:16px;background:#1e1f22;color:#d5d7da}
  h1{font-size:18px} h2{font-size:15px;margin:14px 0 6px}
  .card{background:#2b2d31;border:1px solid #3a3d42;border-radius:8px;padding:12px;margin:10px 0}
  .row{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
  .tag{background:#3a3d42;border-radius:4px;padding:1px 6px;font-size:12px;color:#9ba1a6}
  .ok{color:#4ade80}.bad{color:#f87171}.muted{color:#6b7280}
  pre{background:#000;color:#d5d7da;padding:8px;border-radius:6px;height:260px;overflow:auto;font-size:12px;line-height:1.4}
  input,button,select{background:#3a3d42;color:#d5d7da;border:1px solid #555;border-radius:4px;padding:4px 8px;font-size:13px}
  button{cursor:pointer} button:hover{background:#4a4d52}
  .btn-green{background:#15803d;border-color:#15803d} .btn-red{background:#b91c1c;border-color:#b91c1c}
  img{max-height:120px;border:1px solid #555;border-radius:4px}
  .mac li{margin:4px 0;list-style:none}
  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px}
  .dot.on{background:#4ade80}.dot.off{background:#6b7280}
  #addform{display:flex;gap:6px;flex-wrap:wrap;align-items:center;background:#2b2d31;border:1px solid #3a3d42;border-radius:8px;padding:10px;margin:10px 0}
  #addform input{width:120px}
  .acctag{display:inline-block;margin:4px 6px 0 0;background:#2b2d31;border:1px solid #3a3d42;border-radius:6px;padding:4px 8px}
</style>
</head>
<body>
<h1>EasyBXb 无头控制台 <span id="ver" class="tag muted"></span></h1>
<div>
  <label>账号 <select id="acc"></select></label>
  <label>刷新 <button onclick="poll()">刷新</button></label>
  <button onclick="changeToken()">令牌</button>
</div>
<div id="cards"></div>
<h2>验证码</h2>
<div id="captcha"></div>
<h2>等待输入</h2>
<div id="waitinput"></div>
<h2>宏验证码</h2>
<div id="macrocap"></div>
<h2>输出</h2>
<pre id="out"></pre>
<div class="row">
  <input id="cmd" placeholder="输入指令后回车" style="flex:1">
  <button onclick="sendCmd()">发送</button>
</div>
<h2>宏</h2>
<ul id="mac" class="mac"></ul>
<h2>触发器</h2>
<ul id="trg" class="mac"></ul>
<h2>同步自动化</h2>
<div class="row">
  <input type="file" id="autosync" webkitdirectory multiple style="display:none">
  <button class="btn-green" onclick="document.getElementById('autosync').click()">选择本地 XkxClient 目录一键上传</button>
  <span id="synclabel" class="muted">读取 automation_shared.json 与 accounts/*/automation.json 并覆盖无头端</span>
</div>
<h2>添加账号</h2>
<div id="addform">
  <input id="nu" placeholder="用户名(英文名)">
  <input id="np" type="password" placeholder="密码">
  <input id="nc" placeholder="init 命令(可空,;分隔)">
  <button class="btn-green" onclick="addAccount()">添加并登录</button>
</div>
<div id="acclist"></div>
<script>
let accounts = {};
let token = localStorage.getItem('token') || '';
if(!token){ token = prompt('输入访问令牌:') || ''; localStorage.setItem('token', token); }
function changeToken(){ const t = prompt('新访问令牌:', token); if(t===null||!t.trim())return;
  const r = api('/api/token',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:t.trim()})});
  r.then(x=>{ if(x.ok){ token=t.trim(); localStorage.setItem('token', token); alert('令牌已更新'); poll(); } else alert(x.error||'修改失败'); });
}
function qs(o){return Object.keys(o).map(k=>encodeURIComponent(k)+'='+encodeURIComponent(o[k])).join('&')}
async function api(path, opts){
  const sep = path.includes('?') ? '&' : '?';
  const r = await fetch(path + sep + 'token=' + encodeURIComponent(token), opts); return await r.json();
}
function esc(s){const d=document.createElement('div');d.textContent=(s==null?'':s);return d.innerHTML}
function curAcc(){return document.getElementById('acc').value}
const sigs={};
function sig(id,val){const s=JSON.stringify(val);if(sigs[id]===s)return true;sigs[id]=s;return false;}
function renderStatus(){
  const accSel=document.getElementById('acc'); const prev=accSel.value;
  const cards=document.getElementById('cards'); cards.innerHTML='';
  accSel.innerHTML='';
  const list=Object.keys(accounts).sort();
  for(const a of list){
    const s=accounts[a];
    const opt=document.createElement('option'); opt.value=a; opt.textContent=a+(s.cn_name?(' ('+s.cn_name+')'):''); accSel.appendChild(opt);
    const hp=s.hp||{}; const qi=Array.isArray(hp.qi)?(hp.qi[0]+'/'+hp.qi[1]):'?';
    const jing=Array.isArray(hp.jing)?(hp.jing[0]+'/'+hp.jing[1]):'?';
    const m=s.macro||{}; const st=s.master||{};
    const div=document.createElement('div'); div.className='card';
    div.innerHTML =
      '<div class="row"><b>'+esc(a)+'</b>'+
      '<span class="tag">'+esc(s.cn_name||s.name||'-')+'</span>'+
      '<span class="dot '+(s.connected?'on':'off')+'"></span>'+(s.connected?'已连接':'未连接')+
      (s.logged_in?'<span class="ok">·已登录</span>':'<span class="bad">·未登录</span>')+
      '</div>'+
      '<div class="row muted">房间 '+esc(s.room||'-')+
      ' 气血 '+qi+' 精神 '+jing+
      ' 门派 '+esc(s.family||'-')+' 等级 '+(s.level||0)+'</div>'+
      '<div class="row">触发器:'+(st.triggers?'<span class="ok">开</span>':'<span class="bad">关</span>')+
      ' 定时器:'+(st.timers?'<span class="ok">开</span>':'<span class="bad">关</span>')+
      ' 宏:'+(m.running?'<span class="ok">运行中'+(m.name?(' · '+esc(m.name)):'')+' '+esc(m.progress[0])+'/'+esc(m.progress[1])+'</span>':'<span class="muted">空闲</span>')+
      (s.error?' <span class="bad">ERR '+esc(s.error)+'</span>':'')+
      (s.captcha?' <span class="tag" style="color:#facc15">验证码待处理</span>':'')+
      '</div>';
    cards.appendChild(div);
  }
  if(prev && list.includes(prev)) accSel.value=prev;
}
async function poll(){
  const r=await api('/api/status'); accounts=r.accounts||{};
  document.getElementById('ver').textContent=r.version||'';
  renderStatus(); renderOutput(); renderMacros(); renderTriggers(); renderCaptcha(); renderWaitInput(); renderMacroCap(); renderAccList();
}
async function renderOutput(){
  const a=curAcc(); if(!a) return;
  const r=await api('/api/output?'+qs({account:a,limit:300}));
  const out=document.getElementById('out');
  const text=(r.lines||[]).join('\n');
  if(out.textContent===text) return;   // 内容没变不重写，避免滚动/选中被冲掉
  const nearBottom=out.scrollHeight-out.scrollTop-out.clientHeight<40;
  out.textContent=text;
  if(nearBottom) out.scrollTop=out.scrollHeight;   // 用户往上翻看时不强制拉回底部
}
async function renderMacros(){
  const a=curAcc(); if(!a) return;
  const r=await api('/api/macros?'+qs({account:a}));
  const ul=document.getElementById('mac'); ul.innerHTML='';
  for(const m of (r.macros||[])){
    const li=document.createElement('li');
    li.innerHTML='<span class="dot '+(m.enabled?'on':'off')+'"></span>'+esc(m.name)+
      (m.running?' <span class="ok">运行 '+esc(m.progress[0])+'/'+esc(m.progress[1])+'</span>':'')+
      ' <button class="btn-green" onclick="mac(\'start\',\''+esc(m.name).replace(/\\'/g,"\\'")+'\')">启动</button>'+
      ' <button class="btn-red" onclick="mac(\'stop\',\''+esc(m.name).replace(/\\'/g,"\\'")+'\')">停止</button>';
    ul.appendChild(li);
  }
  const stopAll=document.createElement('li');
  stopAll.innerHTML='<button class="btn-red" onclick="mac(\'stopall\',\'\')">停止当前宏</button>'+
    '<button onclick="mac(\'pause\',\'\')">暂停</button><button onclick="mac(\'resume\',\'\')">恢复</button>';
  ul.appendChild(stopAll);
}
async function renderCaptcha(){
  const a=curAcc(); const c=(accounts[a]||{}).captcha||null; const box=document.getElementById('captcha');
  const v=c&&c.url?(c.kind+'|'+c.url):'';
  if(sig('cap:'+a,v)) return;   // 状态没变则保留输入框内容
  if(!v){ box.innerHTML=''; return; }
  box.innerHTML='<h2>验证码（'+esc(c.kind)+'）</h2><img src="/api/captcha_img?account='+encodeURIComponent(a)+'&t='+Date.now()+'" alt="验证码" onerror="capImgFail(\''+encodeURIComponent(c.url)+'\')">'+
    '<input id="code" placeholder="输入验证码"><button class="btn-green" onclick="sendCode()">提交</button>';
}
async function renderWaitInput(){
  const a=curAcc(); const w=(accounts[a]||{}).wait_input||null; const box=document.getElementById('waitinput');
  const v=w?(w.macro+'|'+w.var+'|'+(w.prompt||'')):'';
  if(sig('wi:'+a,v)) return;
  if(!v){ box.innerHTML=''; return; }
  box.innerHTML='宏「'+esc(w.macro)+'」等待输入'+(w.prompt?'：'+esc(w.prompt):'')+
    ' <input id="wi" placeholder="输入后提交"><button class="btn-green" onclick="sendWaitInput()">提交</button>';
}
async function renderMacroCap(){
  const a=curAcc(); const c=(accounts[a]||{}).macro_captcha||null; const box=document.getElementById('macrocap');
  const v=c&&c.url?(c.var+'|'+c.url):'';
  if(sig('mc:'+a,v)) return;
  if(!v){ box.innerHTML=''; return; }
  box.innerHTML='宏「'+esc(c.macro||c.name||'')+'」需要验证码（变量 '+esc(c.var)+'）'+
    '<br><img src="/api/captcha_img?account='+encodeURIComponent(a)+'&t='+Date.now()+'" alt="验证码" onerror="capImgFail(\''+encodeURIComponent(c.url)+'\')">'+
    '<input id="mc" placeholder="输入验证码"><button class="btn-green" onclick="sendMacroCap()">提交</button>'+
    '<button class="btn-red" onclick="cancelMacroCap()">取消宏</button>';
}
function capImgFail(url){
  const s=document.querySelector('#captcha img, #macrocap img');
  if(!s) return;
  const a=curAcc();
  s.outerHTML='<p class="bad">图片加载失败：<a href="'+decodeURIComponent(url)+'" target="_blank">在新窗口打开</a></p>';
}
async function sendCmd(){const a=curAcc(); const v=document.getElementById('cmd').value; if(!a||!v)return;
  await api('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account:a,cmd:v})});
  document.getElementById('cmd').value=''; setTimeout(renderOutput,300);
}
async function sendCode(){const a=curAcc(); const c=document.getElementById('code').value; if(!a||!c)return;
  await api('/api/fullme',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account:a,code:c})});
  document.getElementById('code').value=''; setTimeout(poll,500);
}
async function sendWaitInput(){const a=curAcc(); const v=document.getElementById('wi').value; if(!a)return;
  await api('/api/input',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account:a,text:v})});
  document.getElementById('wi').value=''; setTimeout(poll,400);
}
async function sendMacroCap(){const a=curAcc(); const v=document.getElementById('mc').value; if(!a||!v)return;
  await api('/api/captcha',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account:a,code:v})});
  document.getElementById('mc').value=''; setTimeout(poll,400);
}
async function cancelMacroCap(){const a=curAcc(); if(!a)return;
  await api('/api/captcha',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account:a,action:'cancel'})});
  setTimeout(poll,400);
}
async function mac(action,name){const a=curAcc(); if(!a)return;
  await api('/api/macro',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account:a,action,name})});
  setTimeout(poll,300);
}
async function trg(action,name,group){const a=curAcc(); if(!a)return;
  const body={account:a,action}; if(name)body.name=name; if(group!==undefined)body.group=group;
  await api('/api/triggers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  setTimeout(poll,300);
}
document.getElementById('autosync').addEventListener('change', async function(){
  const files=Array.from(this.files||[]); this.value='';
  let sharedTxt=null; const accTxt={};
  for(const f of files){
    const rel=(f.webkitRelativePath||f.name).split('/');
    if(rel.length===2 && rel[1]==='automation_shared.json') sharedTxt=await f.text();
    else if(rel.length===3 && rel[0]==='accounts' && rel[2]==='automation.json') accTxt[rel[1]]=await f.text();
  }
  if(!sharedTxt && !Object.keys(accTxt).length){
    alert('未找到 automation_shared.json 或 accounts/*/automation.json\\n请选择本地的 XkxClient 配置目录'); return;
  }
  let body;
  try{
    body={shared: sharedTxt?JSON.parse(sharedTxt):null, accounts:{}};
    for(const [sid,txt] of Object.entries(accTxt)) body.accounts[sid]=JSON.parse(txt);
  }catch(e){ alert('JSON 解析失败：'+e); return; }
  const s=body.shared||{}; const cnt=k=>((s[k]||[]).length);
  const desc='共享：触发器 '+cnt('triggers')+' · 别名 '+cnt('aliases')+' · 定时器 '+cnt('timers')+' · 宏 '+cnt('macros')+
    '\\n账号：'+(Object.keys(body.accounts).join(', ')||'无')+'\\n确认上传并覆盖无头端配置？';
  if(!confirm(desc))return;
  const r=await api('/api/automation_sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(r.ok){
    document.getElementById('synclabel').textContent='上次同步：共享 '+r.shared_items+' 项 · 账号 '+((r.accounts||[]).join(', ')||'无')+' · 已重载 '+(r.reloaded||[]).length+' 个在线账号';
    poll();
  } else alert(r.error||'同步失败');
});
async function renderTriggers(){
  const a=curAcc(); if(!a) return;
  const r=await api('/api/triggers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account:a,action:'list'})});
  const ts=r.triggers||[];
  const ul=document.getElementById('trg'); ul.innerHTML='';
  const head=document.createElement('li');
  const allOn=ts.length>0&&ts.every(t=>t.enabled);
  head.innerHTML='<button class="'+(allOn?'btn-red':'btn-green')+'" onclick="trg(\''+(allOn?'all_off':'all_on')+'\',\'\',undefined)">触发器总'+(allOn?'停用':'启用')+'</button>'+
    '<span class="muted"> 共 '+ts.length+' 条，启用 '+ts.filter(t=>t.enabled).length+' 条</span>';
  ul.appendChild(head);
  const sorted=ts.slice().sort((x,y)=>String(x.group||'').localeCompare(String(y.group||'')));
  let lastGroup=null;
  for(const t of sorted){
    const g=String(t.group||'').trim();
    if(g!==lastGroup){
      lastGroup=g;
      const gh=document.createElement('li');
      const gl=esc(g).replace(/\\'/g,"\\'");
      gh.innerHTML=(g?'<b>📁 '+esc(g)+'</b>':'<b class="muted">未分组</b>')+
        ' <button onclick="trg(\'group_on\',\'\',\''+gl+'\')">全启</button>'+
        ' <button onclick="trg(\'group_off\',\'\',\''+gl+'\')">全停</button>';
      ul.appendChild(gh);
    }
    const li=document.createElement('li');
    li.innerHTML='<span class="dot '+(t.enabled?'on':'off')+'"></span>'+esc(t.name)+
      ' <span class="muted">命中 '+(t.counter||0)+'</span>'+
      ' <button class="'+(t.enabled?'btn-red':'btn-green')+'" onclick="trg(\''+(t.enabled?'one_off':'one_on')+'\',\''+esc(t.name).replace(/\\'/g,"\\'")+'\',undefined)">'+(t.enabled?'停用':'启用')+'</button>';
    ul.appendChild(li);
  }
}
async function addAccount(){
  const u=document.getElementById('nu').value.trim(); if(!u){alert('需要用户名');return}
  const p=document.getElementById('np').value;
  const c=document.getElementById('nc').value.split(';').map(x=>x.trim()).filter(Boolean);
  const r=await api('/api/accounts',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'add',username:u,password:p,init_cmds:c,autologin:true})});
  if(r.ok){document.getElementById('nu').value='';document.getElementById('np').value='';document.getElementById('nc').value='';poll();}
  else alert(r.error||'添加失败');
}
async function removeAccount(a){
  if(!confirm('删除并断开账号 '+a+' ？'))return;
  await api('/api/accounts',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'remove',account:a})});
  poll();
}
async function renderAccList(){
  const r=await api('/api/accounts');
  const box=document.getElementById('acclist'); box.innerHTML='';
  for(const a of (r.accounts||[])){
    const d=document.createElement('span'); d.className='acctag';
    d.innerHTML=esc(a.id)+(a.connected?' <span class="ok">●</span>':' <span class="bad">●</span>')+
      ' <button onclick="removeAccount(\''+esc(a.id).replace(/\\'/g,"\\'")+'\')">删除</button>';
    box.appendChild(d);
  }
}
document.getElementById('cmd').addEventListener('keydown',e=>{if(e.key==='Enter')sendCmd()});
document.getElementById('acc').addEventListener('change',()=>{renderOutput();renderMacros();renderTriggers();renderCaptcha();renderWaitInput();renderMacroCap();});
setInterval(poll,3000); poll();
</script>
</body>
</html>
"""


class HeadlessDaemon(QObject):
    """无头守护：多账号连接 + 自动化引擎 + HTTP 远程控制。"""

    def __init__(self, root: str, config_path: str,
                 control_host: str = "127.0.0.1", control_port: int = 8650) -> None:
        super().__init__()
        self.root = Path(root)
        self.config_path = Path(config_path)
        self.app = XkxApp()
        # 让 ConfigManager 单例指向本守护的配置根目录（桌面客户端在本地已有实例时，
        # 无头进程是独立进程，这里显式替换单例即可）
        cfg = ConfigManager(root=self.root / "XkxClient")
        cfg.bus = self.app.bus
        ConfigManager._instance = cfg
        self.app.config = cfg

        self.bridge = QtBridge()
        self._pump = QTimer(self)
        self._pump.setInterval(30)
        self._pump.timeout.connect(self.bridge.pump)
        self._pump.start()

        self.accounts: dict[str, dict] = {}
        self.outputs: dict[str, deque] = {}
        self.captcha: dict[str, dict] = {}
        self.macro_captcha: dict[str, dict] = {}
        self.pending_input: dict[str, dict] = {}
        self.states: dict[str, dict] = {}

        self._subscribe()
        self._load(config_path)
        self.control = ControlServer(self.bridge, self, control_host, control_port,
                                     token=self._cfg_token)

    # ---- 装配 ----
    def _load(self, config_path: Path) -> None:
        config_path = Path(config_path)
        data = json.loads(config_path.read_text(encoding="utf-8"))
        self._cfg_path = config_path
        self._cfg_data = data
        self._cfg_server = data.get("server") or {}
        self._cfg_token = str(data.get("token") or "")
        for acc in data.get("accounts") or []:
            self._spawn_account(dict(acc), self._cfg_server)

    def _spawn_account(self, acc: dict, server: dict) -> tuple[str, bool]:
        """新建一个账号会话并连接。返回 (sid, 是否新建)。已存在则忽略。"""
        sid = str(acc.get("id") or acc.get("username") or "").strip()
        if not sid or sid in self.accounts:
            return sid, False
        sess = self.app.session(sid)
        sess.macros.headless = True   # 宏验证码不弹 Qt 窗口，交由 Web 控制台处理
        self.accounts[sid] = {"session": sess, "def": dict(acc)}
        self.outputs[sid] = deque(maxlen=500)
        host = acc.get("server") or server
        sess.connect_to(
            host["host"], int(host.get("port", 8080)),
            encoding=host.get("encoding", "gbk"),
            username=acc.get("username"),
            password=acc.get("password"),
            init_cmds=list(acc.get("init_cmds") or []),
            autologin=bool(acc.get("autologin", True)))
        self.states[sid] = {"error": None, "status": "连接中"}
        return sid, True

    def _destroy_account(self, sid: str) -> None:
        acc = self.accounts.pop(sid, None)
        if acc is None:
            return
        try:
            acc["session"].close()
        except Exception:
            pass
        try:
            self.app._sessions.pop(sid, None)
        except Exception:
            pass
        self.outputs.pop(sid, None)
        self.states.pop(sid, None)
        self.captcha.pop(sid, None)
        self.macro_captcha.pop(sid, None)
        self.pending_input.pop(sid, None)

    def _persist_accounts(self) -> None:
        """把当前账号列表（含 Web 添加/删除的变更）写回 headless.json，重启后保留。"""
        try:
            self._cfg_data["accounts"] = [dict(a["def"]) for a in self.accounts.values()]
            # 原子写（tmp+replace）：直写中断会截断文件，下次启动 json.loads 失败守护起不来
            json_write(self._cfg_path, self._cfg_data)
        except Exception:
            traceback.print_exc()

    def _subscribe(self) -> None:
        self.bus.subscribe("net.text_display", self._on_text)
        self.bus.subscribe("net.connected", self._on_connected)
        self.bus.subscribe("net.disconnected", self._on_disconnected)
        self.bus.subscribe("net.connecting", self._on_connecting)
        self.bus.subscribe("net.error", self._on_error)
        self.bus.subscribe("login.done", self._on_login)
        self.bus.subscribe("fullme.detected", self._on_fullme)
        self.bus.subscribe("fullme.grid", self._on_fullme_grid)
        self.bus.subscribe("hongbao.detected", self._on_hongbao)
        self.bus.subscribe("macro.wait_input", self._on_wait_input)
        self.bus.subscribe("macro.captcha", self._on_macro_captcha)

    @property
    def bus(self):
        return self.app.bus

    # ---- 事件 ----
    def _on_text(self, p: dict) -> None:
        aid = p.get("account")
        if aid in self.outputs:
            self.outputs[aid].append(str(p.get("line", "")))

    def _on_connected(self, p: dict) -> None:
        st = self.states.setdefault(p.get("account"), {})
        st["connected"] = True
        st["error"] = None
        st["status"] = "已连接"

    def _on_disconnected(self, p: dict) -> None:
        st = self.states.setdefault(p.get("account"), {})
        st["connected"] = False
        st["logged_in"] = False
        st["status"] = f"已断开（{p.get('reason')}）"

    def _on_connecting(self, p: dict) -> None:
        st = self.states.setdefault(p.get("account"), {})
        st["status"] = str(p.get("status") or "")

    def _on_error(self, p: dict) -> None:
        st = self.states.setdefault(p.get("account"), {})
        st["error"] = str(p.get("msg") or "")

    def _on_login(self, p: dict) -> None:
        st = self.states.setdefault(p.get("account"), {})
        st["logged_in"] = True
        st["status"] = "已登录"

    def _on_fullme(self, p: dict) -> None:
        aid = p.get("account")
        self.captcha[aid] = {"kind": "fullme", "url": str(p.get("url") or ""),
                             "source": str(p.get("source") or "")}

    def _on_fullme_grid(self, p: dict) -> None:
        aid = p.get("account")
        urls = p.get("urls") or []
        self.captcha[aid] = {"kind": "grid", "url": str(urls[0]) if urls else ""}

    def _on_hongbao(self, p: dict) -> None:
        aid = p.get("account")
        self.captcha[aid] = {"kind": "hongbao", "url": str(p.get("url") or "")}

    def _on_wait_input(self, p: dict) -> None:
        aid = p.get("account")
        self.pending_input[aid] = {"var": str(p.get("var") or ""),
                                   "prompt": str(p.get("prompt") or ""),
                                   "name": str(p.get("name") or "")}

    def _on_macro_captcha(self, p: dict) -> None:
        aid = p.get("account")
        self.macro_captcha[aid] = {"kind": "macro", "url": str(p.get("url") or ""),
                                   "var": str(p.get("var") or ""),
                                   "name": str(p.get("name") or "")}

    # ---- 快照 ----
    def _session(self, sid: str):
        acc = self.accounts.get(sid)
        return acc["session"] if acc else None

    def _account_status(self, sid: str) -> dict | None:
        sess = self._session(sid)
        if sess is None:
            return None
        st = dict(self.states.get(sid, {}))
        cs = sess.state
        macros = sess.macros
        running_name = next((n for n in macros.list() if macros.is_running(n)), None)
        cur, total = (0, 0)
        if running_name:
            cur, total = macros.progress(running_name)
        try:
            cn = sess.cn_name()
        except Exception:
            cn = ""
        st.update({
            "account_id": sid,
            "connected": bool(sess.connected),
            "logged_in": bool(sess.logged_in),
            "room": sess.room_name or "",
            "name": cs.name or "",
            "cn_name": cn,
            "family": cs.family or "",
            "level": cs.level or 0,
            "hp": {"qi": cs.qi, "max_qi": cs.max_qi,
                   "jing": cs.jing, "max_jing": cs.max_jing,
                   "jingli": cs.jingli, "max_jingli": cs.max_jingli,
                   "neili": cs.neili, "max_neili": cs.max_neili,
                   "food": cs.food, "water": cs.water},
            "combat": bool(cs.in_combat),
            "enemy": str(cs.enemy.get("enemy_name") or ""),
            "macro": {"running": bool(running_name), "name": running_name,
                      "progress": [cur, total], "paused": macros.is_paused()},
            "master": {"triggers": bool(sess.triggers.master_on),
                       "timers": bool(sess.timers.master_on)},
            "captcha": self.captcha.get(sid),
            "last_line": sess.last_line,
        })
        # 宏「等待输入」状态（引擎仍在等待时才算数，防停止后残留）
        wl = getattr(sess.macros, "_waiting", None)
        if wl and wl[0] in sess.macros._active:
            pi = self.pending_input.get(sid) or {}
            st["wait_input"] = {"macro": wl[0], "var": pi.get("var") or "",
                                "prompt": pi.get("prompt") or ""}
        # 宏「验证码」状态（无头模式）
        if getattr(sess.macros, "headless", False) and getattr(sess.macros, "_captcha_wait", None):
            st["macro_captcha"] = {"url": getattr(sess.macros, "_captcha_url", "") or "",
                                   "var": getattr(sess.macros, "_captcha_var", "") or ""}
        return st

    def _status_all(self) -> dict:
        return {"version": __import__("xkxclient.version", fromlist=["VERSION"]).VERSION,
                "accounts": {sid: s for sid, s in
                             ((a, self._account_status(a)) for a in self.accounts)
                             if s is not None}}

    def _output_json(self, sid: str, limit: int) -> dict:
        buf = self.outputs.get(sid)
        if buf is None:
            return {"lines": [], "error": "unknown account"}
        return {"lines": list(buf)[-max(1, min(limit, 1000)):]}

    def _macro_list_json(self, sid: str) -> dict:
        sess = self._session(sid)
        if sess is None:
            return {"macros": [], "error": "unknown account"}
        out = []
        for name in sess.macros.list():
            m = sess.macros.macros.get(name)
            cur, total = sess.macros.progress(name)
            out.append({"name": name,
                        "enabled": bool(m.enabled if m else True),
                        "group": m.group if m else "",
                        "running": sess.macros.is_running(name),
                        "paused": sess.macros.is_paused(name),
                        "progress": [cur, total]})
        return {"macros": out}

    # ---- 控制动作（全部在 Qt 线程执行）----
    def _cmd(self, data: dict) -> dict:
        sess = self._session(str(data.get("account") or ""))
        if sess is None:
            return {"error": "unknown account"}
        cmd = str(data.get("cmd") or "").strip()
        if not cmd:
            return {"error": "empty command"}
        sess.send(cmd)
        return {"ok": True}

    def _send_auto(self, data: dict) -> dict:
        sess = self._session(str(data.get("account") or ""))
        if sess is None:
            return {"error": "unknown account"}
        cmd = str(data.get("cmd") or "").strip()
        if not cmd:
            return {"error": "empty command"}
        sess.send_auto(cmd)
        return {"ok": True}

    def _input(self, data: dict) -> dict:
        """宏「等待输入」提交：文本写入变量并继续。"""
        sess = self._session(str(data.get("account") or ""))
        if sess is None:
            return {"error": "unknown account"}
        text = str(data.get("text") or "")
        sess.macros.resume_input(text)
        self.pending_input.pop(sess.account_id, None)
        return {"ok": True}

    def _captcha(self, data: dict) -> dict:
        """宏「验证码」提交/取消：验证码写入变量继续，或取消停止宏。"""
        sess = self._session(str(data.get("account") or ""))
        if sess is None:
            return {"error": "unknown account"}
        action = str(data.get("action") or "submit")
        if action == "cancel":
            sess.macros.cancel_captcha()
        else:
            code = str(data.get("code") or "").strip()
            if not code:
                return {"error": "empty code"}
            sess.macros.resume_captcha(code)
        self.macro_captcha.pop(sess.account_id, None)
        return {"ok": True}

    def _token(self, data: dict) -> dict:
        """修改访问令牌：写回 headless.json 并热更新运行中的控制服务。"""
        new = str(data.get("token") or "").strip()
        if not new:
            return {"error": "empty token"}
        self._cfg_token = new
        self._cfg_data["token"] = new
        try:
            json_write(self._cfg_path, self._cfg_data)  # 原子写，防中断截断
        except Exception:
            traceback.print_exc()
        ctrl = getattr(self, "control", None)
        if ctrl is not None:
            ctrl.token = new
            if ctrl.httpd is not None:
                ctrl.httpd.token = new
        return {"ok": True}

    def _automation_sync(self, data: dict) -> dict:
        """Web 一键同步：上传本地自动化配置（共享 + 各账号 automation.json），
        覆盖落盘后热重载全部在线账号引擎。"""
        cfg = ConfigManager.instance()
        shared = data.get("shared")
        n_shared = 0
        if isinstance(shared, dict):
            json_write(cfg.root / "automation_shared.json", shared)
            n_shared = sum(len(shared.get(k) or [])
                           for k in ("triggers", "aliases", "timers", "macros"))
        accs = data.get("accounts") or {}
        saved = []
        for sid, ad in accs.items():
            sid = str(sid).strip()
            if not isinstance(ad, dict) or not sid or "/" in sid or "\\" in sid \
                    or sid in (".", ".."):
                continue  # 非法账号名直接跳过（防路径穿越）
            d = cfg.account_file(sid)
            d.mkdir(parents=True, exist_ok=True)
            json_write(d / "automation.json", ad)
            saved.append(sid)
        reloaded = []
        for sid, acc in self.accounts.items():
            try:
                acc["session"].reload_automation()
                reloaded.append(sid)
            except Exception:
                traceback.print_exc()
        return {"ok": True, "shared_items": n_shared,
                "accounts": saved, "reloaded": reloaded}

    def _macro(self, data: dict) -> dict:
        sess = self._session(str(data.get("account") or ""))
        if sess is None:
            return {"error": "unknown account"}
        action = str(data.get("action") or "")
        name = str(data.get("name") or "")
        if action == "start":
            return {"ok": bool(sess.macros.start(name)), "running": sess.macros.is_running()}
        if action == "stop":
            sess.macros.stop()
            return {"ok": True}
        if action == "stopall":
            sess.macros.stop()
            return {"ok": True}
        if action == "pause":
            sess.macros.pause(name or None)
            return {"ok": True}
        if action == "resume":
            sess.macros.resume(name or None)
            return {"ok": True}
        if action == "list":
            return self._macro_list_json(str(data.get("account") or ""))
        return {"error": f"unknown action {action}"}

    def _set_enabled(self, sid: str, kind: str, enabled: bool,
                     group: str | None = None, name: str | None = None) -> dict:
        """改启用状态并落盘（复用编辑器逻辑：shared 写全局，own 写账号文件）后重载引擎。"""
        sess = self._session(sid)
        if sess is None:
            return {"error": "unknown account"}
        cfg = ConfigManager.instance()
        items = cfg.automation(sid).get(kind, [])
        hit = 0
        for d in items:
            if group is not None:
                if (d.get("group") or "").strip() == group:
                    d["enabled"] = enabled
                    hit += 1
            elif name is not None:
                if d.get("name") == name:
                    d["enabled"] = enabled
                    hit += 1
            else:  # 无 group/name：总开关，全部命中
                d["enabled"] = enabled
                hit += 1
        shared = [d for d in items if d.get("shared")]
        own = [d for d in items if not d.get("shared")]
        cfg.save_automation(None, kind, shared)
        cfg.save_automation(sid, kind, own)
        sess.reload_automation()
        return {"ok": True, "changed": hit}

    def _triggers(self, data: dict) -> dict:
        sid = str(data.get("account") or "")
        action = str(data.get("action") or "")
        group = data.get("group")
        name = data.get("name")
        if action == "all_on":
            return self._set_enabled(sid, "triggers", True)
        if action == "all_off":
            return self._set_enabled(sid, "triggers", False)
        if action == "group_on":
            return self._set_enabled(sid, "triggers", True, group=group)
        if action == "group_off":
            return self._set_enabled(sid, "triggers", False, group=group)
        if action == "one_on":
            return self._set_enabled(sid, "triggers", True, name=name)
        if action == "one_off":
            return self._set_enabled(sid, "triggers", False, name=name)
        if action == "list":
            sess = self._session(sid)
            if sess is None:
                return {"error": "unknown account"}
            return {"triggers": [{"name": t.name, "enabled": t.enabled, "group": t.group,
                                  "counter": t.counter} for t in sess.triggers.triggers]}
        return {"error": f"unknown action {action}"}

    def _timers(self, data: dict) -> dict:
        sid = str(data.get("account") or "")
        action = str(data.get("action") or "")
        name = data.get("name")
        sess = self._session(sid)
        if sess is None:
            return {"error": "unknown account"}
        if action == "all_on":
            sess.timers.enable_all()
            return {"ok": True}
        if action == "all_off":
            sess.timers.disable_all()
            return {"ok": True}
        if action == "one_on":
            sess.timers.start(str(name or ""))
            return {"ok": True}
        if action == "one_off":
            sess.timers.stop(str(name or ""), pause=True)
            return {"ok": True}
        if action == "list":
            return {"timers": [{"name": n, "enabled": t.enabled}
                               for n, t in sess.timers.timers.items()]}
        return {"error": f"unknown action {action}"}

    def _fullme(self, data: dict) -> dict:
        sid = str(data.get("account") or "")
        sess = self._session(sid)
        if sess is None:
            return {"error": "unknown account"}
        code = str(data.get("code") or "").strip()
        if not code:
            return {"error": "empty code"}
        kind = (self.captcha.get(sid) or {}).get("kind")
        if kind == "hongbao":
            sess.send(f"hongbao {code}")
        else:
            sess.send(f"fullme {code}")
        self.captcha.pop(sid, None)
        return {"ok": True}

    def _accounts_manage(self, data: dict) -> dict:
        """Web 添加/删除账号（写入 headless.json 持久化）。"""
        action = str(data.get("action") or "")
        if action == "add":
            acc = {
                "id": str(data.get("id") or "").strip() or str(data.get("username") or "").strip(),
                "username": str(data.get("username") or "").strip(),
                "password": str(data.get("password") or ""),
                "init_cmds": list(data.get("init_cmds") or []),
                "autologin": bool(data.get("autologin", True)),
            }
            if not acc["id"] or not acc["username"]:
                return {"error": "需要用户名"}
            if acc["id"] in self.accounts:
                return {"error": f"账号 {acc['id']} 已存在"}
            sid, created = self._spawn_account(acc, self._cfg_server)
            if not created:
                return {"error": f"账号 {sid} 已存在"}
            self._persist_accounts()
            return {"ok": True, "account": sid}
        if action == "remove":
            sid = str(data.get("account") or data.get("id") or "").strip()
            if sid not in self.accounts:
                return {"error": f"账号 {sid} 不存在"}
            self._destroy_account(sid)
            self._persist_accounts()
            return {"ok": True, "removed": sid}
        return {"error": f"unknown action {action}"}

    def _accounts_list(self) -> dict:
        return {"accounts": [{"id": a, "connected": bool(self._session(a).connected),
                              "logged_in": bool(self._session(a).logged_in)}
                             for a in self.accounts]}

    def _fetch_image(self, url: str) -> tuple[str, bytes] | None:
        """从游戏服务器拉取验证码图片（服务器端转发，避免浏览器混合内容/跨域问题）。"""
        import urllib.request
        try:
            with urllib.request.urlopen(url, timeout=8) as r:
                data = r.read(2 * 1024 * 1024)
            return (r.headers.get("Content-Type") or "image/png", data)
        except Exception:
            traceback.print_exc()
            return None

    # ---- HTTP 路由 ----
    def handle_get(self, handler, path: str) -> None:
        parsed = urlparse(path)
        qs = parse_qs(parsed.query)
        route = parsed.path
        if route in ("/", "/index.html"):
            handler._html(_INDEX_HTML)
            return
        if route == "/api/status":
            ev, res = self.bridge.post(self._status_all)
            ev.wait(timeout=10)
            handler._json(200, res.get("result") if "result" in res else {"error": res.get("error")})
            return
        if route == "/api/output":
            sid = (qs.get("account") or [""])[0]
            limit = int((qs.get("limit") or ["200"])[0])
            ev, res = self.bridge.post(lambda: self._output_json(sid, limit))
            ev.wait(timeout=10)
            handler._json(200, res.get("result") if "result" in res else {"error": res.get("error")})
            return
        if route == "/api/macros":
            sid = (qs.get("account") or [""])[0]
            ev, res = self.bridge.post(lambda: self._macro_list_json(sid))
            ev.wait(timeout=10)
            handler._json(200, res.get("result") if "result" in res else {"error": res.get("error")})
            return
        if route == "/api/accounts":
            ev, res = self.bridge.post(self._accounts_list)
            ev.wait(timeout=10)
            handler._json(200, res.get("result") if "result" in res else {"error": res.get("error")})
            return
        if route == "/api/captcha_img":
            sid = (qs.get("account") or [""])[0]
            # 经桥到 Qt 线程取 URL：HTTP 线程直读字典与 Qt 线程的写入/pop 并发会 KeyError
            ev, res = self.bridge.post(lambda: (
                (self.macro_captcha.get(sid) or {}).get("url")
                or (self.captcha.get(sid) or {}).get("url") or ""))
            ev.wait(timeout=5)
            url = (res.get("result") or "") if "result" in res else ""
            if not url:
                handler._json(404, {"ok": False, "error": "no captcha url"})
                return
            img = self._fetch_image(url)
            if img is None:
                handler._json(502, {"ok": False, "error": "fetch failed"})
                return
            ctype, data = img
            handler._bytes(200, ctype, data)
            return
        handler._json(404, {"ok": False, "error": "not found"})

    def handle_post(self, handler, path: str, data: dict) -> None:
        route = urlparse(path).path
        if route == "/api/command":
            self._post(handler, lambda: self._cmd(data))
        elif route == "/api/send_auto":
            self._post(handler, lambda: self._send_auto(data))
        elif route == "/api/macro":
            self._post(handler, lambda: self._macro(data))
        elif route == "/api/triggers":
            self._post(handler, lambda: self._triggers(data))
        elif route == "/api/timers":
            self._post(handler, lambda: self._timers(data))
        elif route == "/api/fullme":
            self._post(handler, lambda: self._fullme(data))
        elif route == "/api/accounts":
            self._post(handler, lambda: self._accounts_manage(data))
        elif route == "/api/input":
            self._post(handler, lambda: self._input(data))
        elif route == "/api/captcha":
            self._post(handler, lambda: self._captcha(data))
        elif route == "/api/token":
            self._post(handler, lambda: self._token(data))
        elif route == "/api/automation_sync":
            self._post(handler, lambda: self._automation_sync(data))
        else:
            handler._json(404, {"ok": False, "error": "not found"})

    def _post(self, handler, fn) -> None:
        ev, res = self.bridge.post(fn)
        ev.wait(timeout=10)
        if "result" in res:
            r = res["result"]
            if isinstance(r, dict) and r.get("error"):
                handler._json(200, {"ok": False, "error": r["error"]})
            else:
                handler._json(200, {"ok": True, **r})
        else:
            handler._json(500, {"ok": False, "error": res.get("error")})

    # ---- 关闭 ----
    def shutdown(self) -> None:
        self.control.stop()
        self.app.shutdown()