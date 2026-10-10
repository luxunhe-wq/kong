'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const icon = (name, extra = '') => `<svg class="icon ${extra}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const esc = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const defaults = {interval: 2, autoRefresh: true, theme: 'light', cpuThreshold: 85, memoryThreshold: 85, diskThreshold: 90};
let preferences = {...defaults};
try { preferences = {...defaults, ...JSON.parse(localStorage.getItem('monilite.preferences') || localStorage.getItem('kong.preferences') || '{}')}; } catch (_) { /* Local storage is optional. */ }
preferences.interval = [2, 5, 10].includes(Number(preferences.interval)) ? Number(preferences.interval) : 2;
for (const key of ['cpuThreshold', 'memoryThreshold', 'diskThreshold']) preferences[key] = Math.min(100, Math.max(1, Number(preferences[key]) || defaults[key]));
const pages = {
  overview: {title:'服务器概览', breadcrumb:'概览', description:'一览运行状态，让每一份资源都心中有数。', icon:'grid'},
  resources: {title:'资源监控', breadcrumb:'资源监控', description:'从每个核心到每次读写，细看服务器的脉搏。', icon:'chart'},
  docker: {title:'Docker 容器', breadcrumb:'Docker 容器', description:'容器运行状态与资源消耗，一处尽览。', icon:'box'},
  ports: {title:'端口管理', breadcrumb:'端口管理', description:'看清监听端口与运行服务，让每一个入口都有迹可循。', icon:'network'},
  settings: {title:'偏好设置', breadcrumb:'设置', description:'调整监控节奏，打造习惯的工作空间。', icon:'settings'},
};
const state = {page:'overview', data:null, seconds:300, dockerFilter:'all', dockerSearch:'', portSearch:'', portFilter:'all', portScope:'any', portDirection:1, timer:null, fetching:false, failed:false, user:null, csrf:null, authGeneration:0, settingsTab:'preferences', resourceTab:'cpu', storagePath:'/' };
const metricsTimeout = 8000;
state.metricsController = null;
state.metricsStartedAt = 0;
document.body.classList.toggle('dark', preferences.theme === 'dark');

function savePreferences() {
  try { localStorage.setItem('monilite.preferences', JSON.stringify(preferences)); } catch (_) { /* The monitor also works without storage. */ }
}
function bytes(value, decimals = 1) {
  value = Number(value) || 0;
  if (value < 1024) return `${Math.round(value)} B`;
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(decimals)} ${units[index]}`;
}
function splitBytes(value) {
  const [amount, unit] = bytes(value).split(' ');
  return `${amount}<small>${unit}/s</small>`;
}
const number = value => Number(value || 0).toFixed(1);
const clock = timestamp => new Date(timestamp * 1000).toLocaleTimeString('zh-CN', {hour12:false, hour:'2-digit', minute:'2-digit', second:'2-digit'});
function uptime(seconds) {
  const days = Math.floor(seconds / 86400), hours = Math.floor(seconds % 86400 / 3600), minutes = Math.floor(seconds % 3600 / 60);
  return `${days ? `${days} 天 ` : ''}${hours} 小时 ${minutes} 分钟`;
}
function toast(message) {
  const el = $('#toast'); el.textContent = message; el.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { el.hidden = true; }, 2800);
}
function sparkline(key, colorClass = '') {
  const values = (state.data?.history || []).slice(-35).map(p => p[key] || 0);
  if (values.length < 2) return '<svg class="metric-spark" viewBox="0 0 90 36" aria-hidden="true"><path d="M0 27H90" opacity=".2"/></svg>';
  const max = Math.max(...values, key === 'rx' ? 1024 : 15), min = Math.max(0, Math.min(...values) - max * .15);
  const path = values.map((v, i) => `${i ? 'L' : 'M'}${(i / (values.length - 1) * 90).toFixed(1)} ${(31 - (v - min) / Math.max(max - min, 1) * 26).toFixed(1)}`).join(' ');
  return `<svg class="metric-spark ${colorClass}" viewBox="0 0 90 36" aria-label="近期真实数据趋势"><path d="${path}"/></svg>`;
}
function metricCard({label, glyph, tag, value, color='', key, meta, percent, resource}) {
  const element=resource?'a':'article';
  const link=resource?` href="#resources/${resource}" data-resource="${resource}" aria-label="查看${label}详情"`:'';
  return `<${element} class="metric-card ${color} ${resource?'metric-card-link':''}"${link}><div class="metric-top"><span class="metric-icon">${icon(glyph)}</span><span class="metric-label">${label}</span><span class="metric-tag">${tag}</span></div><div class="metric-value-row"><div class="metric-value">${value}</div>${sparkline(key)}</div><div class="metric-meta">${meta}</div>${percent != null ? `<div class="metric-meter"><span style="width:${Math.min(100,percent)}%"></span></div>` : '<div class="metric-meter"><span style="width:100%;opacity:.12"></span></div>'}${resource?`<span class="metric-open">查看详情 ${icon('arrow')}</span>`:''}</${element}>`;
}
function metrics() {
  const d = state.data, disk = d.disks[0];
  return metricCard({resource:'cpu', label:'CPU 使用率', glyph:'cpu', tag:`${d.system.cpu_logical} 核心`, key:'cpu', value:`${number(d.cpu.percent)}<small>%</small>`, percent:d.cpu.percent, meta:`<span class="dot"></span><strong>${d.cpu.percent >= preferences.cpuThreshold ? '负载较高' : '运行平稳'}</strong><span>· 平均负载 ${number(d.cpu.load[0])}</span>`}) +
    metricCard({resource:'memory', label:'内存使用', glyph:'memory', tag:bytes(d.memory.total,0), color:'purple', key:'memory', value:`${number(d.memory.percent)}<small>%</small>`, percent:d.memory.percent, meta:`<strong>${bytes(d.memory.used)}</strong><span>/ ${bytes(d.memory.total)} · 可用 ${bytes(d.memory.available)}</span>`}) +
    metricCard({resource:'disk', label:'磁盘空间', glyph:'disk', tag:'根目录 /', color:'blue', key:'disk', value:`${number(disk?.percent)}<small>%</small>`, percent:disk?.percent || 0, meta:disk ? `<strong>${bytes(disk.used)}</strong><span>/ ${bytes(disk.total)} · 已使用</span>` : '暂无可用文件系统'}) +
    metricCard({resource:'network', label:'网络流量', glyph:'network', tag:'实时速率', color:'orange', key:'rx', value:splitBytes(d.network.rx_rate), meta:`<span style="color:var(--green)">↓</span><strong>${bytes(d.network.rx_rate)}/s</strong><span style="margin-left:5px;color:var(--orange)">↑</span><strong>${bytes(d.network.tx_rate)}/s</strong>`});
}
function rangeButtons() {
  return `<div class="range-buttons" aria-label="趋势时间范围">${[[300,'5 分钟'],[900,'15 分钟'],[3600,'1 小时']].map(([value,label])=>`<button data-range="${value}" class="${state.seconds===value?'active':''}" aria-pressed="${state.seconds===value}">${label}</button>`).join('')}</div>`;
}
function chartPanel(id, title, subtitle, series) {
  return `<section class="panel chart-panel" data-chart="${id}"><div class="panel-heading"><div class="panel-header-copy"><div class="panel-title"><h2>${title}</h2></div><p class="panel-subtitle">${subtitle}</p></div>${rangeButtons()}</div><div class="chart-legend">${series.map(s=>`<span class="legend-item"><span class="dot" style="background:${s.color}"></span>${s.label}<strong data-legend="${s.key}">—</strong></span>`).join('')}</div><div class="chart-wrap"><div class="chart-y-labels"></div><svg class="chart-svg" viewBox="0 0 600 160" preserveAspectRatio="none" role="img" aria-label="${title}实时趋势图"></svg><div class="chart-x-labels"></div><div class="chart-tooltip" hidden></div></div><div class="chart-footer"><span>${icon('clock')}本机每 2 秒采集 · <span data-sample-count>等待采样</span></span><span><span class="dot"></span>真实数据</span></div></section>`;
}
const chartConfigs = {
  performance: {series:[{key:'cpu',label:'CPU',color:'var(--green)'},{key:'memory',label:'内存',color:'var(--purple)'}], type:'percent'},
  cpu: {series:[{key:'cpu',label:'CPU 使用率',color:'var(--green)'}], type:'percent'},
  memory: {series:[{key:'memory',label:'内存使用率',color:'var(--purple)'}], type:'percent'},
  network: {series:[{key:'rx',label:'接收',color:'var(--green)'},{key:'tx',label:'发送',color:'var(--orange)'}], type:'bytes'},
  diskio: {series:[{key:'disk_read',label:'读取',color:'var(--blue)'},{key:'disk_write',label:'写入',color:'var(--purple)'}], type:'bytes'},
};
function drawCharts() {
  $$('[data-chart]').forEach(panel => {
    const id = panel.dataset.chart, config = chartConfigs[id], history = state.data.history;
    const svg = $('.chart-svg',panel), wrap = $('.chart-wrap',panel);
    const now = state.data.timestamp, earliest = history[0]?.timestamp ?? now;
    const start = Math.max(now-state.seconds, Math.min(earliest,now-30));
    let max = Math.max(...history.flatMap(p=>config.series.map(s=>p[s.key] || 0)),1);
    max = config.type==='percent' ? Math.max(20,Math.ceil(max/20)*20) : Math.max(1024,Math.ceil(max * 1.2 / (1024 ** Math.floor(Math.log2(max)/10))) * (1024 ** Math.floor(Math.log2(max)/10)));
    const x = timestamp => Math.max(0,Math.min(600,(timestamp-start)/(now-start)*600));
    const y = value => 156 - (value/max)*152;
    const grid = [0,.25,.5,.75,1].map(t=>`<line class="chart-grid" x1="0" x2="600" y1="${4+t*152}" y2="${4+t*152}"/>`).join('');
    let paths = '';
    config.series.forEach((series,index)=>{
      const points = history.map(p=>`${x(p.timestamp).toFixed(2)},${y(p[series.key] || 0).toFixed(2)}`);
      const last = history[history.length-1];
      if (points.length>1) {
        const path = `M${points.join(' L')}`;
        if (!index) paths += `<defs><linearGradient id="fill-${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${series.color}" stop-opacity=".1"/><stop offset="100%" stop-color="${series.color}" stop-opacity=".005"/></linearGradient></defs><path d="${path} L${x(last.timestamp)},156 L${x(history[0].timestamp)},156 Z" fill="url(#fill-${id})"/>`;
        paths += `<path class="chart-line" d="${path}" stroke="${series.color}"/>`;
      }
      if(last) paths += `<circle cx="${x(last.timestamp)}" cy="${y(last[series.key] || 0)}" r="2.4" fill="${series.color}"/>`;
      const legend = $(`[data-legend="${series.key}"]`,panel);
      if(legend) legend.textContent = config.type==='percent' ? `${number(last?.[series.key])}%` : `${bytes(last?.[series.key])}/s`;
    });
    svg.innerHTML = grid+paths+'<line class="chart-cursor" x1="0" x2="0" y1="0" y2="160" visibility="hidden"/>';
    $('.chart-y-labels',panel).innerHTML = [1,.75,.5,.25,0].map(t=>`<span>${config.type==='percent' ? `${Math.round(max*t)}%` : bytes(max*t,0)}</span>`).join('');
    $('.chart-x-labels',panel).innerHTML = [0,.25,.5,.75,1].map(t=>`<span>${clock(start+(now-start)*t)}</span>`).join('');
    $('[data-sample-count]',panel).textContent = `已记录 ${history.length} 个采样点`;
    svg.onpointermove = event => {
      if(!history.length) return;
      const rect = svg.getBoundingClientRect(), ratio = Math.min(1,Math.max(0,(event.clientX-rect.left)/rect.width));
      const target = start + ratio*(now-start);
      const point = history.reduce((a,b)=>Math.abs(a.timestamp-target)<Math.abs(b.timestamp-target)?a:b);
      const cursor = $('.chart-cursor',svg), position = x(point.timestamp);
      cursor.setAttribute('x1',position); cursor.setAttribute('x2',position); cursor.setAttribute('visibility','visible');
      const tooltip = $('.chart-tooltip',wrap);
      tooltip.innerHTML = `<strong>${clock(point.timestamp)}</strong><br>${config.series.map(s=>`<span style="color:${s.color}">${s.label}</span> ${config.type==='percent'?`${number(point[s.key])}%`:`${bytes(point[s.key])}/s`}`).join('<br>')}`;
      tooltip.hidden = false;
      tooltip.style.left = `${Math.min(Math.max(12,event.clientX-wrap.getBoundingClientRect().left+12),wrap.clientWidth-tooltip.offsetWidth-5)}px`;
      tooltip.style.top = '6px';
    };
    svg.onpointerleave = () => { $('.chart-tooltip',wrap).hidden=true; $('.chart-cursor',svg).setAttribute('visibility','hidden'); };
  });
}
function alerts() {
  if (!state.data) return [];
  const d = state.data, list = [];
  if(state.failed || Date.now()/1000-d.timestamp>12) list.push({title:'监控连接中断',detail:'正在等待最新采集数据，请检查服务连接。'});
  if(d.cpu.percent>=preferences.cpuThreshold) list.push({title:`CPU 使用率 ${number(d.cpu.percent)}%`,detail:`超过设置的 ${preferences.cpuThreshold}% 提醒阈值。`});
  if(d.memory.percent>=preferences.memoryThreshold) list.push({title:`内存使用率 ${number(d.memory.percent)}%`,detail:`可用内存 ${bytes(d.memory.available)}。`});
  d.disks.filter(disk=>disk.percent>=preferences.diskThreshold).forEach(disk=>list.push({title:`磁盘 ${disk.mount} 使用率 ${number(disk.percent)}%`,detail:`剩余 ${bytes(disk.free)}，请留意存储空间。`}));
  if(!d.docker.available && !d.docker.loading) list.push({title:'Docker 暂不可用',detail:d.docker.error});
  d.docker.containers.filter(c=>c.status.includes('(unhealthy)') || ['restarting','dead'].includes(c.state)).forEach(c=>list.push({title:`容器 ${c.name} 需要关注`,detail:c.status}));
  return list;
}
function serverInfo() {
  const d = state.data, list = alerts();
  return `<div class="panel-heading"><div class="panel-title"><h2>服务器信息</h2></div><span class="health-tag ${list.length?'warning':''}"><span class="dot"></span>${list.length?'需要关注':'运行正常'}</span></div><div class="host-card"><span class="host-icon">${icon('server')}</span><div><strong title="${esc(d.system.hostname)}">${esc(d.system.hostname)}</strong><p>LOCAL SERVER / ${esc(d.system.architecture)}</p></div><span class="dot"></span></div><dl class="server-info"><div><dt>操作系统</dt><dd title="${esc(d.system.os)}">${esc(d.system.os)}</dd></div><div><dt>内核版本</dt><dd title="${esc(d.system.kernel)}">${esc(d.system.kernel)}</dd></div><div><dt>处理器</dt><dd>${d.system.cpu_physical} 物理核 / ${d.system.cpu_logical} 逻辑核</dd></div><div><dt>运行时间</dt><dd>${uptime(d.system.uptime)}</dd></div><div><dt>活跃进程</dt><dd>${d.process_count} 个进程</dd></div></dl><div class="server-footer">${icon(list.length?'info':'check')}${list.length?`${list.length} 项指标需要关注`:'所有核心指标正常'}<time>${clock(d.timestamp)}</time></div>`;
}
function diskWidget(disk) {
  if(!disk) return '<div class="empty-state">暂无文件系统数据</div>';
  const d = state.data;
  return `<div class="disk-usage"><div class="disk-title">${icon('disk')}<strong>${esc(disk.mount === '/'?'系统磁盘':disk.mount)}</strong><span class="tag">${disk.mount==='/'?'ROOT /':esc(disk.filesystem).toUpperCase()}</span></div><div class="disk-amount"><span>${bytes(disk.used).split(' ')[0]}<small>${bytes(disk.used).split(' ')[1]} / ${bytes(disk.total)}</small></span><strong>${number(disk.percent)}%</strong></div><div class="disk-progress"><span style="width:${disk.percent}%;${disk.percent>=preferences.diskThreshold?'background:var(--orange)':''}"></span></div><div class="disk-legend"><span><span class="dot"></span>已用 ${bytes(disk.used)}</span><span><span class="dot"></span>可用 ${bytes(disk.free)}</span></div></div><div class="disk-io-row"><div><small>磁盘读取</small><strong>${bytes(d.disk_io.read_rate)}<span> /s</span></strong></div><div><small>磁盘写入</small><strong>${bytes(d.disk_io.write_rate)}<span> /s</span></strong></div></div>`;
}
function statusBadge(container) {
  const running = container.state==='running';
  const warning = container.status.includes('(unhealthy)') || ['restarting','dead'].includes(container.state);
  const names = {running:'运行中',exited:'已停止',created:'已创建',paused:'已暂停',restarting:'重启中',dead:'异常',removing:'移除中'};
  return `<span class="status-badge ${warning?'warning':running?'':'stopped'}"><span class="dot"></span>${warning && running?'不健康':names[container.state] || esc(container.state)}</span>`;
}
function dockerRows(containers, compact=false) {
  return containers.map(c=>`<tr data-container="${esc(c.id)}" tabindex="0" aria-label="查看容器 ${esc(c.name)}"><td><div class="container-name"><span class="container-box">${icon('box')}</span><div><strong>${esc(c.name)}</strong><small>${esc(c.id.slice(0,12))}${compact?'':` · ${esc(c.image)}`}</small></div></div></td><td>${statusBadge(c)}</td><td><span class="table-value">${number(c.cpu)}<small>%</small></span></td><td><div class="table-meter"><span class="table-value">${compact?number(c.memory_percent)+'%':esc(c.memory_usage.split(' / ')[0])}</span><span class="table-meter-track"><span style="width:${Math.min(c.memory_percent,100)}%"></span></span></div></td>${compact?'':`<td>${esc(c.network_io)}</td><td>${esc(c.pids)}</td>`}<td><button class="table-action" data-detail="${esc(c.id)}" aria-label="查看 ${esc(c.name)} 详情">${icon('chevron')}</button></td></tr>`).join('');
}
function emptyDocker(colspan, filtered=false) {
  const docker = state.data.docker;
  return `<tr><td colspan="${colspan}"><div class="empty-state">${icon('box')}<strong>${docker.loading?'正在连接 Docker…':docker.error?esc(docker.error):filtered?'没有匹配的容器':'还没有 Docker 容器'}</strong><p>${docker.loading?'首次统计约需几秒钟。':docker.error?'系统资源监控不受影响。':filtered?'试试其他名称或状态筛选。':'创建容器后会自动在这里显示。'}</p></div></td></tr>`;
}
function dockerSummary() {
  const docker=state.data.docker;
  return `<span><strong>${docker.containers.length}</strong> 个容器</span><span><span class="dot"></span><strong>${docker.running || 0}</strong> 运行中</span><span><span class="dot" style="background:#b0bac3"></span><strong>${docker.stopped || 0}</strong> 已停止</span>`;
}
function overviewPage() {
  return `<div class="metrics-grid" id="metrics-grid"></div><div class="dashboard-top">${chartPanel('performance','资源使用趋势','细微变化，即刻掌握',chartConfigs.performance.series)}<section class="panel server-panel" id="server-info"></section></div><div class="dashboard-bottom"><section class="panel"><div class="panel-heading"><div class="panel-title">${icon('box')}<h2>Docker 容器</h2><span class="tag" id="docker-total-tag">0</span></div><a class="button button-text" href="#docker">查看全部 ${icon('arrow')}</a></div><div class="docker-summary" id="docker-summary"></div><div class="table-scroll"><table><thead><tr><th>容器名称</th><th>状态</th><th>CPU</th><th>内存</th><th></th></tr></thead><tbody id="overview-containers"></tbody></table></div><div class="table-footer"><span>${icon('refresh')}容器统计每 10 秒更新</span><a href="#docker">进入容器工作空间 →</a></div></section><section class="panel operations-panel"><div class="panel-heading"><div class="panel-header-copy"><div class="panel-title"><h2>运维快捷入口</h2></div><p class="panel-subtitle">从指标出发，找到具体原因。</p></div></div><div class="operations-links"><a href="#resources/disk">${icon('disk')}<div><strong>磁盘占用分析</strong><span>目录排行 · 大文件定位</span></div>${icon('chevron')}</a><a href="#resources/cpu">${icon('cpu')}<div><strong>查找高负载进程</strong><span>核心用量 · CPU 进程排行</span></div>${icon('chevron')}</a><a href="#resources/memory">${icon('memory')}<div><strong>排查内存占用</strong><span>内存分布 · 进程占用</span></div>${icon('chevron')}</a></div></section></div>`;
}
function resourcesPage() {
  return resourceDetailsPage();
}
function dockerPage() {
  return `<div class="metrics-grid" id="docker-metrics"></div><section class="panel"><div class="panel-heading"><div class="panel-title">${icon('box')}<h2>容器列表</h2></div><button class="button button-text" data-refresh>${icon('refresh')}刷新数据</button></div><div class="filter-tabs" aria-label="容器状态筛选">${[['all','全部'],['running','运行中'],['stopped','已停止']].map(([key,label])=>`<button data-filter="${key}" class="${state.dockerFilter===key?'active':''}">${label}<small data-count="${key}">0</small></button>`).join('')}</div><div class="toolbar"><label class="filter-search">${icon('search')}<input id="docker-search" placeholder="搜索容器名称、ID 或镜像" value="${esc(state.dockerSearch)}" aria-label="搜索容器"></label><span class="toolbar-note" id="docker-updated"></span></div><div class="table-scroll"><table><thead><tr><th>容器名称 / 镜像</th><th>状态</th><th>CPU</th><th>内存使用</th><th>网络 I/O</th><th>进程数</th><th></th></tr></thead><tbody id="docker-table-body"></tbody></table></div><div class="table-footer"><span id="docker-results-count"></span><span>点击容器查看详情</span></div></section>`;
}
function settingsPage() {
  const preferencesPanel = `<section class="panel settings-panel"><div class="settings-section"><h3>显示偏好</h3><p>偏好设置保存在当前浏览器，下次访问自动恢复。</p><div class="setting-row"><div class="setting-label"><strong>界面主题</strong><span>选择适合工作环境的明暗风格</span></div><div class="theme-options"><button data-theme="light" class="${preferences.theme==='light'?'active':''}">${icon('sun')}浅色</button><button data-theme="dark" class="${preferences.theme==='dark'?'active':''}">${icon('moon')}深色</button></div></div></div><div class="settings-section"><h3>实时监控</h3><p>后台每 2 秒持续采集，趋势数据保留最近 1 小时；Docker 每 10 秒、端口每 5 秒独立采集。</p><div class="setting-row"><div class="setting-label"><strong>自动刷新</strong><span>自动获取最新指标，关闭后可手动刷新</span></div><button class="toggle" id="auto-refresh-toggle" role="switch" aria-label="自动刷新" aria-checked="${preferences.autoRefresh}"></button></div><div class="setting-row"><div class="setting-label"><strong>界面刷新频率</strong><span>调整浏览器获取数据的间隔</span></div><select class="setting-select" id="refresh-interval" aria-label="刷新频率">${[2,5,10].map(v=>`<option value="${v}" ${preferences.interval===v?'selected':''}>每 ${v} 秒</option>`).join('')}</select></div><div class="setting-row"><div class="setting-label"><strong>立即刷新</strong><span>获取一份当前资源快照</span></div><button class="button" data-refresh>${icon('refresh')}刷新数据</button></div></div><div class="settings-section"><h3>资源提醒</h3><p>指标达到阈值后，顶部通知和服务器状态将自动提示。</p>${[['cpuThreshold','CPU 使用率'],['memoryThreshold','内存使用率'],['diskThreshold','磁盘使用率']].map(([key,label])=>`<div class="setting-row"><div class="setting-label"><strong>${label}</strong><span>达到此百分比时提示资源负载较高</span></div><label><input class="threshold-input" type="number" min="1" max="100" step="1" value="${preferences[key]}" data-threshold="${key}" aria-label="${label}提醒阈值"> <span style="color:var(--muted);font-size:11px">%</span></label></div>`).join('')}</div><div class="settings-section"><div class="about-monilite"><span class="brand-mark">M</span><div><h3>MoniLite.</h3><p>轻量的本机资源监控 · 全部数据来自真实采集<br>Python + psutil · 原生 Web 界面<br><a class="button button-text" href="https://github.com/luxunhe-wq/kong" target="_blank" rel="noopener noreferrer" aria-label="查看 MoniLite GitHub 项目（新标签页）">GitHub 项目 ${icon('external')}</a></p></div><span class="about-version">v1.4.0</span></div></div></section>`;
  return accountSettingsLayout(preferencesPanel);
}
function renderPage() {
  const metadata = pages[state.page];
  $('#page-title').innerHTML = `${metadata.title}<span class="heading-dot">.</span>`;
  $('#page-description').textContent = metadata.description;
  $('#breadcrumb-page').textContent = metadata.breadcrumb;
  document.title = `${metadata.breadcrumb} · MoniLite`;
  $$('[data-page]').forEach(a=>a.classList.toggle('active',a.dataset.page===state.page));
  if(!state.data) return;
  $('#loading').hidden = true; $('#page-content').hidden = false;
  $('#page-content').innerHTML = ({overview:overviewPage,resources:resourcesPage,docker:dockerPage,ports:portsPage,settings:settingsPage})[state.page]();
  bindPage(); updatePage();
}
function updatePage() {
  if(!state.data) return;
  const d=state.data, docker=d.docker;
  $('#nav-docker-count').textContent=docker.loading?'…':docker.containers.length;
  if($('#metrics-grid')) {
    const focused=document.activeElement?.closest('[data-resource]')?.dataset.resource;
    $('#metrics-grid').innerHTML=metrics();
    if(focused)$(`[data-resource="${focused}"]`)?.focus({preventScroll:true});
  }
  if(state.page==='overview') {
    $('#server-info').innerHTML=serverInfo();
    $('#docker-summary').innerHTML=dockerSummary();
    $('#docker-total-tag').textContent=docker.containers.length;
    $('#overview-containers').innerHTML=docker.containers.length?dockerRows(docker.containers.slice(0,4),true):emptyDocker(5);
  } else if(state.page==='resources') {
    updateResourceDetails();
  } else if(state.page==='docker') {
    $('#docker-metrics').innerHTML=[
      {label:'容器总数',glyph:'box',tag:'ALL CONTAINERS',value:String(docker.containers.length),key:'cpu',meta:`<strong>${docker.running||0}</strong><span>运行中 · ${docker.stopped||0} 个已停止</span>`},
      {label:'运行中',glyph:'check',tag:'RUNNING',value:String(docker.running||0),key:'cpu',meta:`<span class="dot"></span><span>${docker.available?'Docker 已连接':docker.loading?'正在连接 Docker':'Docker 不可用'}</span>`},
      {label:'容器 CPU',glyph:'cpu',tag:'累计使用率',value:`${number(docker.containers.reduce((sum,c)=>sum+c.cpu,0))}<small>%</small>`,color:'purple',key:'cpu',meta:'按单核统计，多核使用可超过 100%'},
      {label:'容器进程',glyph:'process',tag:'PIDS',value:String(docker.containers.reduce((sum,c)=>sum+(parseInt(c.pids)||0),0)),color:'blue',key:'memory',meta:'所有容器中运行的进程总数'},
    ].map(card=>metricCard(card).replace(/<svg class="metric-spark[\s\S]*?<\/svg>/,'')).join('');
    $$('[data-count]').forEach(el=>el.textContent=el.dataset.count==='all'?docker.containers.length:el.dataset.count==='running'?docker.running||0:docker.stopped||0);
    $('#docker-updated').textContent=docker.loading?'正在采集容器统计…':`上次采集 ${clock(docker.updated_at)}`;
    updateDockerTable();
  } else if(state.page==='ports') {
    updatePorts();
  }
  drawCharts();
  updateStatus();
  if($('#notification-popover').hidden===false) renderNotifications();
}
function updateDockerTable() {
  const search=state.dockerSearch.toLowerCase();
  const rows=state.data.docker.containers.filter(c=>(state.dockerFilter==='all'||(state.dockerFilter==='running'?c.state==='running':c.state!=='running'))&&`${c.name} ${c.id} ${c.image}`.toLowerCase().includes(search));
  $('#docker-table-body').innerHTML=rows.length?dockerRows(rows):emptyDocker(7,Boolean(search)||state.dockerFilter!=='all');
  $('#docker-results-count').textContent=`显示 ${rows.length} / ${state.data.docker.containers.length} 个容器`;
}
function updateStatus() {
  const status=$('#connection-status'), list=alerts();
  const stale=state.data && Date.now()/1000-state.data.timestamp>12;
  status.classList.toggle('offline',state.failed || !preferences.autoRefresh || stale);
  status.innerHTML=`<span class="dot ${!state.failed && preferences.autoRefresh && !stale?'pulse':''}"></span>${state.failed?'连接中断':stale?'采集延迟':preferences.autoRefresh?'实时监控中':'自动刷新已暂停'}`;
  $('#notification-dot').hidden=list.length===0;
  $('#notification-button').setAttribute('aria-label',list.length?`${list.length} 条资源状态提醒`:'资源状态正常');
  if(state.data) $('#last-updated').textContent=`最后更新 ${clock(state.data.timestamp)} · ${preferences.autoRefresh?`${preferences.interval}s 自动刷新`:'已暂停'}`;
}
function renderNotifications() {
  const list=alerts();
  $('#notification-popover').innerHTML=`<h3>资源状态 <span class="tag">${list.length} 条提醒</span></h3>${list.length?list.map(item=>`<div class="notification-item"><span class="dot" style="background:var(--orange)"></span><div>${esc(item.title)}<small>${esc(item.detail)}</small></div></div>`).join(''):`<div class="notification-item"><span class="dot"></span><div>服务器运行平稳<small>CPU、内存、磁盘与 Docker 均无异常提醒。</small></div></div>`}<a class="button button-text" href="#settings" id="notification-settings">调整提醒阈值 ${icon('arrow')}</a>`;
}
function bindPage() {
  const container=$('#page-content');
  container.onclick=async event=>{
    if(handleResourceClick(event) || handleAccountClick(event) || handlePortClick(event)) return;
    const range=event.target.closest('[data-range]');
    if(range) { state.seconds=Number(range.dataset.range); $$('[data-range]').forEach(button=>{button.classList.toggle('active',Number(button.dataset.range)===state.seconds);button.setAttribute('aria-pressed',Number(button.dataset.range)===state.seconds);}); await fetchData(); return; }
    const filter=event.target.closest('[data-filter]');
    if(filter) {state.dockerFilter=filter.dataset.filter; $$('[data-filter]').forEach(button=>button.classList.toggle('active',button.dataset.filter===state.dockerFilter)); updateDockerTable();return;}
    const row=event.target.closest('[data-container]');
    if(row) { openContainer(row.dataset.container); return; }
    const refresh=event.target.closest('[data-refresh]');
    if(refresh) {refresh.disabled=true;const success=await fetchData();refresh.disabled=false;toast(success?'资源数据已刷新':'刷新失败，请检查连接');return;}
    const theme=event.target.closest('[data-theme]');
    if(theme) {preferences.theme=theme.dataset.theme;document.body.classList.toggle('dark',preferences.theme==='dark');$$('[data-theme]').forEach(button=>button.classList.toggle('active',button.dataset.theme===preferences.theme));savePreferences();return;}
    if(event.target.closest('#auto-refresh-toggle')) {preferences.autoRefresh=!preferences.autoRefresh;$('#auto-refresh-toggle').setAttribute('aria-checked',preferences.autoRefresh);savePreferences();scheduleRefresh();updateStatus();if(preferences.autoRefresh)fetchData();}
  };
  container.onkeydown=event=>{const row=event.target.closest('[data-container]');if(row&&(event.key==='Enter'||event.key===' ')){event.preventDefault();openContainer(row.dataset.container);}};
  if($('#docker-search')) $('#docker-search').oninput=event=>{state.dockerSearch=event.target.value;updateDockerTable();};
  if(state.page==='ports') bindPorts();
  if($('#refresh-interval')) $('#refresh-interval').onchange=event=>{preferences.interval=Number(event.target.value);savePreferences();scheduleRefresh();updateStatus();toast('刷新频率已更新');};
  $$('[data-threshold]').forEach(input=>input.onchange=()=>{const value=Math.min(100,Math.max(1,Math.round(Number(input.value)||defaults[input.dataset.threshold])));input.value=value;preferences[input.dataset.threshold]=value;savePreferences();updateStatus();toast('提醒阈值已保存');});
  if(state.page==='settings') bindAccountSettings();
  if(state.page==='resources') bindResourceDetails();
}
function openContainer(id) {
  const c=state.data.docker.containers.find(c=>c.id===id);if(!c)return;
  $('#detail-content').innerHTML=`<div class="detail-header"><div><h2>${esc(c.name)}</h2><p>${esc(c.image)}</p></div><button class="icon-button" id="detail-close" aria-label="关闭详情">${icon('close')}</button></div><div class="detail-body"><dl>${[['容器 ID',esc(c.id)],['状态',statusBadge(c)+' '+esc(c.status)],['CPU 使用率',`${number(c.cpu)}%`],['内存使用',`${esc(c.memory_usage)} (${number(c.memory_percent)}%)`],['网络 I/O',esc(c.network_io)],['磁盘 I/O',esc(c.block_io)],['进程数量',esc(c.pids)],['端口映射',esc(c.ports||'无公开端口')],['创建时间',esc(c.created)],['采集时间',clock(state.data.docker.updated_at)]].map(([label,value])=>`<div><dt>${label}</dt><dd>${value}</dd></div>`).join('')}</dl><div class="detail-note">这是打开详情时的资源快照。容器 CPU 按单核统计，多核任务可超过 100%；内存百分比基于 Docker 的容器内存限制。</div></div>`;
  $('#detail-close').onclick=()=>$('#detail-dialog').close();
  if(!$('#detail-dialog').open)$('#detail-dialog').showModal();
}
function cancelMetricsRequest() {
  const controller=state.metricsController;
  state.metricsController=null;state.metricsStartedAt=0;state.fetching=false;
  controller?.abort();
}
async function fetchData() {
  if(!state.user || state.authBusy)return false;
  if(state.fetching) {
    // Background tabs can suspend both fetch and its timeout. Use elapsed wall time on return.
    if(Date.now()-state.metricsStartedAt<metricsTimeout)return false;
    cancelMetricsRequest();
  }
  state.fetching=true;
  const generation=state.authGeneration;
  const controller=new AbortController();
  state.metricsController=controller;state.metricsStartedAt=Date.now();
  const isCurrent=()=>generation===state.authGeneration && state.metricsController===controller;
  let requestTimeout;
  try {
    requestTimeout=setTimeout(()=>controller.abort(),metricsTimeout);
    const response=await fetch(`/api/metrics?seconds=${state.seconds}`,{cache:'no-store',signal:controller.signal});
    if(!isCurrent())return false;
    if(response.status===401){handleSessionExpired();return false;}
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const data=await response.json();
    if(!isCurrent())return false;
    const first=!state.data;state.data=data;state.failed=false;
    $('#connection-error').hidden=true;
    if(first)renderPage();else updatePage();
    return true;
  } catch(error) {
    if(!isCurrent() || !state.user)return false;
    state.failed=true;
    $('#connection-error').textContent=`无法连接监控服务${state.data?'，当前显示上一次采集的数据':''}。${preferences.autoRefresh?'正在自动重试…':'请手动刷新页面重试。'}`;
    $('#connection-error').hidden=false;
    if(!state.data)$('#loading').innerHTML=`${icon('server')}<strong>暂时无法连接服务器</strong><p>请确认 MoniLite 服务已启动${preferences.autoRefresh?'，系统会自动重试。':'。'}</p>`;
    updateStatus();return false;
  } finally {
    clearTimeout(requestTimeout);
    if(isCurrent()){state.fetching=false;state.metricsController=null;state.metricsStartedAt=0;}
  }
}
function scheduleRefresh() {
  clearTimeout(state.timer);
  state.timer=null;
  if(state.user && !state.authBusy && preferences.autoRefresh)state.timer=setTimeout(()=>{
    // Keep retrying even if a previous request never settles after suspension or a network change.
    scheduleRefresh();fetchData();
  },preferences.interval*1000);
}
function resumeRefresh(event) {
  if(document.hidden || !state.user || state.authBusy || !preferences.autoRefresh)return;
  if(event.type==='online' || (event.type==='pageshow' && event.persisted))cancelMetricsRequest();
  scheduleRefresh();fetchData();
}
function navigate() {
  const [route,query='']=location.hash.slice(1).split('?');
  const [requested='overview',detail]=route.split('/');
  const target=requested==='processes'?'ports':requested;
  const savedScroll=state.page==='resources' && state.resourceTab==='disk' && target==='resources' && detail==='disk'?window.scrollY:0;
  state.page=pages[target]?target:'overview';
  if(state.page==='resources'){
    state.resourceTab=['cpu','memory','disk','network'].includes(detail)?detail:'cpu';
    state.storagePath=new URLSearchParams(query).get('path')||'/';
  }
  stopStoragePolling();
  renderPage();closeSidebar();
  $('#notification-popover').hidden=true;$('#notification-button').setAttribute('aria-expanded','false');
  window.scrollTo({top:savedScroll,behavior:'instant'});
}
function closeSidebar() {document.body.classList.remove('sidebar-open');$('#sidebar').classList.remove('open');$('#sidebar-overlay').classList.remove('open');$('#menu-button').setAttribute('aria-expanded','false');}

function searchResults() {
  const query=$('#global-search').value.trim().toLowerCase(), results=[];
  for(const [key,page] of Object.entries(pages))if(!query||`${page.title} ${key}`.toLowerCase().includes(query))results.push({type:'page',id:key,label:page.title,description:'页面',icon:page.icon});
  if(state.data && query){
    state.data.docker.containers.filter(c=>`${c.name} ${c.id} ${c.image}`.toLowerCase().includes(query)).slice(0,8).forEach(c=>results.push({type:'container',id:c.id,label:c.name,description:'Docker 容器',icon:'box'}));
    portData().items.filter(row=>portSearchText(row).includes(query)).slice(0,8).forEach(row=>results.push({type:'port',id:row.id,label:`${row.protocol.toUpperCase()} · ${portEndpoint(row)}`,description:portService(row),icon:'network'}));
  }
  $('#search-results').innerHTML=results.length?results.map(r=>`<button class="search-result" data-result-type="${r.type}" data-result-id="${esc(r.id)}">${icon(r.icon)}<span>${esc(r.label)}</span><small>${esc(r.description)}</small></button>`).join(''):'<div class="empty-state">没有找到匹配的页面或资源</div>';
}
function openSearch() {if(!state.user)return;$('#search-dialog').showModal();$('#global-search').value='';searchResults();$('#global-search').focus();}
$('#search-button').onclick=openSearch;
$('#search-close').onclick=()=>$('#search-dialog').close();
$('#global-search').oninput=searchResults;
$('#search-results').onclick=event=>{
  const result=event.target.closest('[data-result-type]');if(!result)return;
  $('#search-dialog').close();
  if(result.dataset.resultType==='page')location.hash=result.dataset.resultId;
  else if(result.dataset.resultType==='container')openContainer(result.dataset.resultId);
  else if(result.dataset.resultType==='port')openPort(result.dataset.resultId);
};
$('#global-search').onkeydown=event=>{if(event.key==='Enter')$('.search-result')?.click();if(event.key==='ArrowDown'){event.preventDefault();$('.search-result')?.focus();}};
$('#search-results').onkeydown=event=>{const results=$$('.search-result'),index=results.indexOf(document.activeElement);if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();results[(index+(event.key==='ArrowDown'?1:-1)+results.length)%results.length]?.focus();}};
document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key==='k'){event.preventDefault();if(!$('#search-dialog').open)openSearch();}if(event.key==='Escape'){closeSidebar();$('#notification-popover').hidden=true;$('#notification-button').setAttribute('aria-expanded','false');}});
$('#menu-button').onclick=()=>{const open=$('#sidebar').classList.toggle('open');document.body.classList.toggle('sidebar-open',open);$('#sidebar-overlay').classList.toggle('open',open);$('#menu-button').setAttribute('aria-expanded',open);};
$('#sidebar-overlay').onclick=closeSidebar;
function syncSidebarViewport() {
  $('#sidebar').style.setProperty('--sidebar-height',`${window.visualViewport?.height || window.innerHeight}px`);
  if(window.innerWidth>640 && $('#sidebar').classList.contains('open'))closeSidebar();
}
syncSidebarViewport();
window.addEventListener('resize',syncSidebarViewport,{passive:true});
window.visualViewport?.addEventListener('resize',syncSidebarViewport,{passive:true});
$('#notification-button').onclick=()=>{const el=$('#notification-popover');el.hidden=!el.hidden;$('#notification-button').setAttribute('aria-expanded',!el.hidden);if(!el.hidden)renderNotifications();};
document.addEventListener('click',event=>{if(!event.target.closest('.notification-wrap')){$('#notification-popover').hidden=true;$('#notification-button').setAttribute('aria-expanded','false');}});
for(const dialog of $$('dialog'))dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close();}});
$('#export-button').onclick=()=>{
  if(!state.data){toast('请等待首次采集完成');return;}
  const report={project:'MoniLite',version:'1.4.0',exported_at:new Date().toISOString(),...state.data};
  const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download=`monilite-report-${new Date().toISOString().replace(/[:.]/g,'-')}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('监控报告已导出');
};
$('#today').textContent=new Date().toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric',weekday:'short'});
window.addEventListener('hashchange',navigate);
document.addEventListener('visibilitychange',resumeRefresh);
for(const event of ['focus','pageshow','online'])window.addEventListener(event,resumeRefresh);
document.addEventListener('resume',resumeRefresh);
