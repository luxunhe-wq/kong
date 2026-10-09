'use strict';

const storageView={job:null,timer:null,epoch:0,table:'children',filter:''};
const resourceLabels={cpu:'CPU',memory:'内存',disk:'磁盘',network:'网络'};

function resourceTabs() {
  return `<nav class="resource-tabs" aria-label="资源详情分类">${[['cpu','cpu'],['memory','memory'],['disk','disk'],['network','network']].map(([key,glyph])=>`<a href="#resources/${key}" class="${state.resourceTab===key?'active':''}" ${state.resourceTab===key?'aria-current="page"':''}>${icon(glyph)}${resourceLabels[key]}<span>${icon('chevron')}</span></a>`).join('')}</nav>`;
}
function resourceProcessPanel() {
  return `<section class="panel"><div class="panel-heading"><div class="panel-header-copy"><div class="panel-title"><h2>${state.resourceTab==='cpu'?'CPU':'内存'} 占用较高的进程</h2></div><p class="panel-subtitle">按当前用量排序，展示前 8 项。</p></div></div><div class="table-scroll section-gap"><table><thead><tr><th>进程 / PID</th><th>用户</th><th>CPU</th><th>内存</th></tr></thead><tbody id="resource-processes"></tbody></table></div></section>`;
}
function resourceDetailsPage() {
  let content='';
  if(state.resourceTab==='cpu') {
    content=`${chartPanel('cpu','CPU 使用趋势','观察负载变化，再定位具体核心与进程。',chartConfigs.cpu.series)}<div class="resource-facts" id="cpu-facts"></div><div class="section-grid"><section class="panel"><div class="panel-heading"><div class="panel-header-copy"><div class="panel-title">${icon('cpu')}<h2>各核心使用率</h2></div><p class="panel-subtitle" id="cpu-model"></p></div></div><div class="core-grid" id="core-grid"></div></section>${resourceProcessPanel()}</div>`;
  } else if(state.resourceTab==='memory') {
    content=`${chartPanel('memory','内存使用趋势','结合缓存、交换空间与进程用量排查内存消耗。',chartConfigs.memory.series)}<div class="section-grid section-gap"><section class="panel"><div class="panel-heading"><div class="panel-title">${icon('memory')}<h2>内存与交换空间</h2></div></div><div class="info-grid" id="memory-info"></div><p class="resource-note">已用内存按总内存减去可用内存计算；可回收缓存不计入已用内存。</p></section>${resourceProcessPanel()}</div>`;
  } else if(state.resourceTab==='network') {
    content=`${chartPanel('network','网络吞吐量','主网卡汇总，排除桥接与常见隧道接口，避免重复统计。',chartConfigs.network.series)}<div class="resource-facts" id="network-facts"></div><section class="panel"><div class="panel-heading"><div class="panel-title">${icon('network')}<h2>网络接口明细</h2></div></div><div class="table-scroll section-gap"><table class="network-table"><thead><tr><th>接口 / IP</th><th>状态</th><th>接收速率</th><th>发送速率</th><th>累计接收</th><th>累计发送</th></tr></thead><tbody id="network-interfaces"></tbody></table></div></section>`;
  } else {
    content=`<div class="resource-disk-top">${chartPanel('diskio','磁盘读写趋势','实时观察块设备读写，目录占用通过下方分析定位。',chartConfigs.diskio.series)}<section class="panel"><div class="panel-heading"><div class="panel-title">${icon('disk')}<h2>文件系统</h2></div></div><div id="all-disks" class="filesystem-list"></div></section></div>${storagePanel()}`;
  }
  return `${resourceTabs()}<div class="resource-detail-content">${content}</div>`;
}
function updateResourceDetails() {
  const d=state.data;
  if(state.resourceTab==='cpu') {
    $('#cpu-model').textContent=d.system.cpu_model;
    $('#cpu-facts').innerHTML=[['1 分钟平均负载',d.cpu.load[0].toFixed(2)],['5 分钟平均负载',d.cpu.load[1].toFixed(2)],['15 分钟平均负载',d.cpu.load[2].toFixed(2)],['逻辑核心',`${d.system.cpu_logical} 核`]].map(([label,value])=>fact(label,value)).join('');
    $('#core-grid').innerHTML=d.cpu.cores.map((value,index)=>`<div class="core-item"><div><span>Core ${String(index).padStart(2,'0')}</span><strong>${number(value)}%</strong></div><div class="metric-meter"><span style="width:${value}%;${value>=preferences.cpuThreshold?'background:var(--orange)':''}"></span></div></div>`).join('');
  } else if(state.resourceTab==='memory') {
    $('#memory-info').innerHTML=[['总内存',bytes(d.memory.total)],['已使用',bytes(d.memory.used)],['可用内存',bytes(d.memory.available)],['页面缓存',bytes(d.memory.cached)],['交换空间',bytes(d.memory.swap_total)],['交换已用',bytes(d.memory.swap_used)]].map(([label,value])=>`<div class="info-tile"><small>${label}</small><strong>${value}</strong></div>`).join('');
  } else if(state.resourceTab==='network') {
    $('#network-facts').innerHTML=[['累计接收',bytes(d.network.received)],['累计发送',bytes(d.network.sent)],['在线接口',`${d.network.interfaces.filter(i=>i.up).length} 个`],['全部接口',`${d.network.interfaces.length} 个`]].map(([label,value])=>fact(label,value)).join('');
    $('#network-interfaces').innerHTML=d.network.interfaces.map(net=>`<tr><td><strong>${esc(net.name)}</strong><br><small style="color:var(--muted)">${esc(net.address)}</small></td><td><span class="status-badge ${net.up?'':'stopped'}"><span class="dot"></span>${net.up?'在线':'离线'}</span></td><td>${bytes(net.rx_rate)}/s</td><td>${bytes(net.tx_rate)}/s</td><td>${bytes(net.received)}</td><td>${bytes(net.sent)}</td></tr>`).join('');
  } else {
    $('#all-disks').innerHTML=d.disks.map(disk=>`<div class="filesystem-item"><div class="filesystem-title"><strong>${esc(disk.mount)}</strong><span class="tag">${esc(disk.filesystem).toUpperCase()}</span><span>${number(disk.percent)}%</span></div><p>${esc(disk.device)}</p><div class="disk-progress"><span style="width:${disk.percent}%"></span></div><div class="filesystem-meta"><span>已用 ${bytes(disk.used)} / ${bytes(disk.total)}</span><span>可用 ${bytes(disk.free)}</span></div>${state.user.role==='admin'?`<a class="button button-text" href="#resources/disk?path=${encodeURIComponent(disk.mount)}">分析此磁盘 ${icon('arrow')}</a>`:''}</div>`).join('');
  }
  if($('#resource-processes')) {
    const key=state.resourceTab==='cpu'?'cpu':'memory';
    const processes=key==='memory'?(d.memory_processes||d.processes):d.processes;
    $('#resource-processes').innerHTML=[...processes].sort((a,b)=>b[key]-a[key]).slice(0,8).map(p=>`<tr><td><strong>${esc(p.name)}</strong><small class="process-id">PID ${p.pid}</small></td><td>${esc(p.user)}</td><td>${number(p.cpu)}%</td><td>${bytes(p.memory)}</td></tr>`).join('');
  }
}
function fact(label,value) {return `<div><small>${label}</small><strong>${esc(value)}</strong></div>`;}

function storagePanel() {
  if(state.user.role!=='admin')return `<section class="panel section-gap"><div class="panel-heading"><div class="panel-title">${icon('disk')}<h2>目录占用分析</h2></div></div><div class="empty-state"><strong>此功能仅对管理员开放</strong><p>目录路径与文件信息属于运维权限，当前账号可查看上方资源指标。</p></div></section>`;
  return `<section class="panel section-gap storage-panel" id="storage-panel"><div class="panel-heading"><div class="panel-header-copy"><div class="panel-title"><h2>目录占用分析</h2><span class="tag">只读分析</span></div><p class="panel-subtitle">逐级查看目录与大文件，找出空间用在哪里。</p></div><button class="button" id="storage-export" disabled>${icon('download')}导出分析</button></div><form class="storage-path-form" id="storage-path-form"><label for="storage-path">分析目录</label><div><input class="form-input" id="storage-path" value="${esc(state.storagePath)}" placeholder="例如 /var/log" maxlength="4096" required aria-label="分析目录路径"><button type="submit" class="button button-primary" id="storage-scan-button">${icon('search')}分析目录</button><button type="button" class="button" id="storage-rescan" title="重新读取目录，更新快照">${icon('refresh')}重新扫描</button></div></form><div class="storage-quick-paths"><span>快速定位</span>${[['/','根目录'],['/var','服务数据'],['/var/log','系统日志'],['/var/lib/docker','Docker 数据'],['/root','管理员目录']].map(([path,label])=>`<button data-storage-path="${esc(path)}">${label}</button>`).join('')}</div><nav class="storage-breadcrumb" id="storage-breadcrumb" aria-label="目录层级"></nav><div class="storage-error form-message error" id="storage-error" role="alert" hidden></div><div class="storage-progress" id="storage-progress" role="status"><span class="loading-spinner"></span><div><strong>正在准备目录分析</strong><span>后台读取文件大小，监控指标照常更新。</span></div></div><div id="storage-result" hidden><div class="storage-summary" id="storage-summary"></div><div class="storage-result-toolbar"><div class="filter-tabs"><button class="active" data-storage-table="children">当前目录</button><button data-storage-table="files">大文件排行</button></div><label class="filter-search">${icon('search')}<input id="storage-filter" value="${esc(storageView.filter)}" placeholder="筛选文件名或路径" aria-label="筛选分析结果"></label></div><div class="table-scroll"><table class="storage-table"><thead id="storage-table-head"></thead><tbody id="storage-table-body"></tbody></table></div><div class="table-footer" id="storage-table-footer"></div><div class="storage-insights" id="storage-insights"></div><details class="storage-notes"><summary>统计范围与说明</summary><div id="storage-notes"></div></details></div></section>`;
}
function stopStoragePolling() {clearTimeout(storageView.timer);storageView.epoch++;}
function resetStorageView() {stopStoragePolling();storageView.job=null;storageView.filter='';storageView.table='children';}
function storageCurrent(epoch) {return epoch===storageView.epoch && state.user?.role==='admin' && state.page==='resources' && state.resourceTab==='disk' && Boolean($('#storage-panel'));}
function bindResourceDetails() {
  if(state.resourceTab!=='disk' || state.user.role!=='admin')return;
  $('#storage-path-form').onsubmit=event=>{event.preventDefault();navigateStorage($('#storage-path').value.trim());};
  $('#storage-filter').oninput=event=>{storageView.filter=event.target.value;renderStorageTable();};
  const matching=storageView.job?.path===state.storagePath && (storageView.job.status==='running' || Date.now()/1000-storageView.job.started_at<=300);
  if(matching) {
    renderStorage();
    if(storageView.job.status==='running')pollStorage(storageView.epoch);
  } else {
    storageView.job=null;storageView.filter='';storageView.table='children';
    startStorageScan(state.storagePath);
  }
}
function navigateStorage(path) {
  const target=`#resources/disk?path=${encodeURIComponent(path)}`;
  if(location.hash===target){startStorageScan(path);return;}
  location.hash=target;
}
async function startStorageScan(path,force=false) {
  stopStoragePolling();const epoch=storageView.epoch;
  if(!storageCurrent(epoch))return;
  $('#storage-error').hidden=true;
  setScanBusy(true);$('#storage-progress').hidden=false;
  $('#storage-progress strong').textContent='正在准备目录分析';
  try {
    const result=await apiRequest('/api/storage/scan',{method:'POST',body:{path,force}});
    if(!storageCurrent(epoch))return;
    storageView.job=result;state.storagePath=result.path;
    $('#storage-path').value=result.path;
    history.replaceState(null,'',`#resources/disk?path=${encodeURIComponent(result.path)}`);
    renderStorage();
    if(result.status==='running')pollStorage(epoch);
  } catch(error) {
    if(storageCurrent(epoch)){$('#storage-error').textContent=error.message;$('#storage-error').hidden=false;$('#storage-progress').hidden=true;setScanBusy(false);}
  }
}
async function pollStorage(epoch) {
  storageView.timer=setTimeout(async()=>{
    if(!storageCurrent(epoch))return;
    try {
      const result=await apiRequest(`/api/storage/scan?id=${encodeURIComponent(storageView.job.id)}`);
      if(!storageCurrent(epoch))return;
      storageView.job=result;renderStorage();
      if(result.status==='running')pollStorage(epoch);
    } catch(error) {
      if(storageCurrent(epoch)){$('#storage-error').textContent=error.message;$('#storage-error').hidden=false;$('#storage-progress').hidden=true;setScanBusy(false);}
    }
  },1000);
}
function setScanBusy(busy) {
  for(const id of ['storage-scan-button','storage-rescan'])if($(`#${id}`))$(`#${id}`).disabled=busy;
  $('#storage-export').disabled=busy || !storageView.job || storageView.job.status==='error';
}
function renderStorage() {
  const job=storageView.job;if(!job||!$('#storage-result'))return;
  const parts=job.path.split('/').filter(Boolean);
  $('#storage-breadcrumb').innerHTML=`<button data-storage-path="/">${icon('server')}根目录</button>${parts.map((name,index)=>`${icon('chevron')}<button data-storage-path="${esc('/'+parts.slice(0,index+1).join('/'))}">${esc(name)}</button>`).join('')}`;
  const running=job.status==='running';
  setScanBusy(running);$('#storage-progress').hidden=!running;
  if(running){$('#storage-result').hidden=true;$('#storage-progress strong').textContent=`正在分析 ${job.path}`;$('#storage-progress div>span').textContent=`已读取 ${job.entries_scanned.toLocaleString()} 项 · 已统计 ${bytes(job.bytes_scanned)} · ${job.elapsed} 秒`;return;}
  if(job.status==='error'){$('#storage-result').hidden=true;$('#storage-error').textContent=job.error;$('#storage-error').hidden=false;return;}
  $('#storage-result').hidden=false;
  $('#storage-error').hidden=true;
  const status=job.status==='partial'?'部分结果':'分析完成';
  $('#storage-summary').innerHTML=[['已统计磁盘占用',bytes(job.bytes_scanned)],['读取条目',job.entries_scanned.toLocaleString()],['当前目录项目',String(job.items_count)],['快照状态',status]].map(([label,value])=>fact(label,value)).join('');
  $('#storage-notes').innerHTML=job.notes.map(note=>`<p>${esc(note)}</p>`).join('');
  if(job.status==='partial')$('#storage-summary').classList.add('partial');else $('#storage-summary').classList.remove('partial');
  $$('[data-storage-table]').forEach(button=>button.classList.toggle('active',button.dataset.storageTable===storageView.table));
  renderStorageTable();renderStorageInsights();
}
function renderStorageTable() {
  const job=storageView.job;if(!job||!$('#storage-table-body'))return;
  const files=storageView.table==='files', list=files?job.largest_files:job.items;
  const query=storageView.filter.toLowerCase();
  const filtered=list.map((item,index)=>({item,index})).filter(({item})=>`${item.name} ${item.path}`.toLowerCase().includes(query));
  $('#storage-table-head').innerHTML=`<tr><th>${files?'文件 / 完整路径':'目录与文件'}</th><th>实际占用</th><th>占比</th><th>修改时间</th><th></th></tr>`;
  $('#storage-table-body').innerHTML=filtered.length?filtered.map(({item,index})=>`<tr><td><button class="storage-entry" data-storage-entry="${index}" data-entry-table="${storageView.table}" ${item.excluded?'disabled':''}>${icon(item.type==='directory'?'box':'info')}<span><strong>${esc(item.name)}</strong><small>${files?esc(item.path):item.excluded?'虚拟目录或其他文件系统 · 已跳过':item.type==='directory'?'目录 · 点击进入':item.type==='symlink'?'符号链接 · 不跟随':'文件 · 查看信息'}</small></span></button></td><td><strong>${bytes(item.size)}</strong></td><td><div class="storage-share"><span>${(item.size/Math.max(job.bytes_scanned,1)*100).toFixed(1)}%</span><i><span style="width:${Math.min(100,item.size/Math.max(job.bytes_scanned,1)*100)}%"></span></i></div></td><td>${esc(dateLabel(item.modified_at))}</td><td><button class="table-action" data-copy-path="${esc(item.path)}" aria-label="复制 ${esc(item.name)} 路径" title="复制路径">${icon('external')}</button></td></tr>`).join(''):`<tr><td colspan="5"><div class="empty-state">${query?'没有匹配的文件或目录':files?'此目录中未找到非空普通文件':'此目录没有可展示的项目'}</div></td></tr>`;
  $('#storage-table-footer').innerHTML=`<span>显示 ${filtered.length} 项 · ${files?'已扫描范围内最大的 50 个文件':'按占用排序，最多展示 200 项'}</span><span>快照 ${clock(job.finished_at)} · 用时 ${job.elapsed}s</span>`;
}
function renderStorageInsights() {
  const job=storageView.job;
  const top=job.items.find(item=>!item.excluded&&item.size>0);
  const tips=[];
  if(top?.type==='directory')tips.push(`优先进入 ${top.name}：该目录在当前已扫描结果中占用最多，继续下钻能定位具体来源。`);
  if(job.largest_files.some(file=>/\.log(?:\.|$)/i.test(file.name)))tips.push('发现日志文件：可以结合修改时间和所属服务检查日志轮转配置。');
  if(job.path.startsWith('/var/lib/docker'))tips.push('Docker 数据建议通过 Docker 的镜像、容器和数据卷管理工具处理，先确认哪些资源仍在使用。');
  if(job.status==='partial')tips.push('当前是部分结果，请进入具体子目录继续分析，以获得更完整的占用排行。');
  if(!tips.length)tips.push('切换到大文件排行查看完整路径与文件信息，也可以导出分析快照，进一步排查空间消耗。');
  $('#storage-insights').innerHTML=`<h3>${icon('info')}排查建议</h3>${tips.map(tip=>`<p>${esc(tip)}</p>`).join('')}`;
}
async function copyStoragePath(path, message='路径已复制') {
  try {
    if(navigator.clipboard && window.isSecureContext)await navigator.clipboard.writeText(path);
    else {
      const input=document.createElement('textarea');input.value=path;input.className='clipboard-input';document.body.append(input);input.select();
      const copied=document.execCommand('copy');input.remove();if(!copied)throw new Error('Copy failed');
    }
    toast(message);
  } catch(error) {toast('复制失败，可在详情中选择文本复制');}
}
function openStorageFile(item) {
  $('#detail-content').innerHTML=`<div class="detail-header"><div><h2>${esc(item.name)}</h2><p>文件信息 · 分析快照</p></div><button class="icon-button" id="detail-close" aria-label="关闭文件信息">${icon('close')}</button></div><div class="detail-body"><dl>${[['完整路径',esc(item.path)],['类型',item.type==='symlink'?'符号链接':'文件'],['实际磁盘占用',bytes(item.size)],['逻辑大小',bytes(item.logical_size)],['修改时间',esc(dateLabel(item.modified_at))]].map(([key,value])=>`<div><dt>${key}</dt><dd>${value}</dd></div>`).join('')}</dl><div class="detail-note">实际占用按磁盘块计算，稀疏文件和压缩文件系统可能与逻辑大小不同。此处仅查看文件属性。</div><div class="dialog-form-actions"><button class="button" id="file-copy-path">复制完整路径</button><button class="button button-primary" id="file-open-parent">查看所在目录</button></div></div>`;
  $('#detail-close').onclick=()=>$('#detail-dialog').close();
  $('#file-copy-path').onclick=()=>copyStoragePath(item.path);
  $('#file-open-parent').onclick=()=>{$('#detail-dialog').close();navigateStorage(item.path.slice(0,item.path.lastIndexOf('/'))||'/');};
  $('#detail-dialog').showModal();
}
function handleResourceClick(event) {
  const path=event.target.closest('[data-storage-path]');if(path){navigateStorage(path.dataset.storagePath);return true;}
  if(event.target.closest('#storage-rescan')){startStorageScan(state.storagePath,true);return true;}
  const table=event.target.closest('[data-storage-table]');if(table){storageView.table=table.dataset.storageTable;$$('[data-storage-table]').forEach(button=>button.classList.toggle('active',button.dataset.storageTable===storageView.table));renderStorageTable();return true;}
  const entry=event.target.closest('[data-storage-entry]');
  if(entry){const list=entry.dataset.entryTable==='files'?storageView.job.largest_files:storageView.job.items;const item=list[Number(entry.dataset.storageEntry)];if(item?.browseable)navigateStorage(item.path);else if(item&&!item.excluded)openStorageFile(item);return true;}
  const copy=event.target.closest('[data-copy-path]');if(copy){copyStoragePath(copy.dataset.copyPath);return true;}
  if(event.target.closest('#storage-export')) {
    const url=URL.createObjectURL(new Blob([JSON.stringify({project:'kong',exported_at:new Date().toISOString(),analysis:storageView.job},null,2)],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;link.download=`kong-storage-${Date.now()}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('目录分析已导出');return true;
  }
  return false;
}
