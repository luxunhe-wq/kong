'use strict';

const portScopes = {local:'仅本机', all:'全部网卡', interface:'指定网卡'};
const portStatuses = {listening:'监听中', bound:'UDP 已绑定', published:'Docker 已发布'};
const portData = () => state.data?.ports || {items:[], loading:true};
const portEndpoint = row => `${row.family==='IPv6'?`[${row.address}]`:row.address}:${row.port}`;
const portService = row => row.containers.length ? row.containers.map(c=>c.name).join(' / ') : row.processes.map(p=>p.service || p.name).join(' / ') || '未识别服务';
const portSearchText = row => `${row.port} ${row.protocol} ${row.address} ${row.family} ${portScopes[row.scope]} ${portService(row)} ${row.processes.map(p=>`${p.pid} ${p.name} ${p.user} ${p.service}`).join(' ')} ${row.containers.map(c=>`${c.name} ${c.image} ${c.target_port}`).join(' ')}`.toLowerCase();

function portsPage() {
  return `<div class="resource-facts ports-facts" id="ports-summary"></div><section class="panel ports-panel"><div class="panel-heading"><div class="panel-header-copy"><div class="panel-title">${icon('network')}<h2>监听端口与服务</h2><span class="tag" id="ports-total">—</span></div><p class="panel-subtitle">从端口找到进程，从进程找到服务。</p></div><button class="button button-text" data-refresh>${icon('refresh')}刷新数据</button></div><div class="filter-tabs port-filter-tabs" aria-label="端口协议筛选">${[['all','全部'],['tcp','TCP'],['udp','UDP'],['docker','Docker 映射']].map(([key,label])=>`<button data-port-filter="${key}" class="${state.portFilter===key?'active':''}" aria-pressed="${state.portFilter===key}">${label}<small data-port-count="${key}">0</small></button>`).join('')}</div><div class="toolbar ports-toolbar"><label class="filter-search">${icon('search')}<input id="port-search" placeholder="搜索端口、进程、服务或容器" aria-label="搜索端口" value="${esc(state.portSearch)}"></label><select id="port-scope" class="setting-select" aria-label="端口绑定范围">${[['any','所有绑定范围'],...Object.entries(portScopes)].map(([key,label])=>`<option value="${key}" ${state.portScope===key?'selected':''}>${label}</option>`).join('')}</select><span class="toolbar-note" id="ports-updated"></span></div><div class="form-message error ports-error" id="ports-error" role="alert" hidden></div><div class="table-scroll"><table class="ports-table"><thead><tr><th><button class="table-sort" data-port-sort>端口 <span id="port-sort-indicator">↑</span></button></th><th>协议</th><th>绑定地址</th><th>绑定范围</th><th>运行服务 / 进程</th><th>PID</th><th>状态</th><th></th></tr></thead><tbody id="port-table-body"></tbody></table></div><div class="table-footer"><span id="port-results-count"></span><span>点击端口查看进程与容器归属</span></div><div class="port-inventory-note">${icon('info')}<p>TCP 展示监听端口，UDP 展示未连接的绑定端口；Docker 映射包含通过 NAT 发布的端口。监听或发布不代表公网可访问，实际可达性还取决于防火墙、路由和安全组。</p></div></section>`;
}

function filteredPorts() {
  const query=state.portSearch.trim().toLowerCase();
  return portData().items.filter(row=>(state.portFilter==='all' || row.protocol===state.portFilter || (state.portFilter==='docker' && row.containers.length)) && (state.portScope==='any' || row.scope===state.portScope) && (!query || portSearchText(row).includes(query))).sort((a,b)=>(a.port-b.port)*state.portDirection || a.protocol.localeCompare(b.protocol) || a.address.localeCompare(b.address));
}

function updatePorts() {
  if(!$('#port-table-body'))return;
  const data=portData(), items=data.items, rows=filteredPorts();
  const tcp=items.filter(r=>r.protocol==='tcp').length, udp=items.filter(r=>r.protocol==='udp').length, docker=items.filter(r=>r.containers.length).length;
  $('#ports-summary').innerHTML=[['TCP 监听 / 映射',tcp,'连接型服务'],['UDP 绑定 / 映射',udp,'数据报服务'],['Docker 映射',docker,'关联已发布的容器端口'],['仅本机绑定',items.filter(r=>r.scope==='local').length,'回环地址上的服务']].map(([label,value,note])=>`<div><small>${label}</small><strong>${data.loading?'…':value}</strong><span>${note}</span></div>`).join('');
  $('#ports-total').textContent=`${items.length} 项`;
  $$('[data-port-count]').forEach(el=>el.textContent=({all:items.length,tcp,udp,docker})[el.dataset.portCount]);
  $('#ports-updated').textContent=data.loading?'正在采集端口…':`每 5 秒采集 · ${data.updated_at?clock(data.updated_at):'等待采集'}`;
  const warnings=[data.error,data.docker_error, data.docker_loading?'Docker 映射正在采集，当前结果可能尚未完整':null];
  if(data.updated_at && Date.now()/1000-data.updated_at>20)warnings.push('端口采集延迟，当前显示上一次结果');
  $('#ports-error').textContent=warnings.filter(Boolean).join('；');$('#ports-error').hidden=!warnings.some(Boolean);
  $('#port-sort-indicator').textContent=state.portDirection===1?'↑':'↓';
  $('#port-table-body').innerHTML=rows.length?rows.map(row=>`<tr><td><button class="port-number" data-port-detail="${esc(row.id)}" aria-label="查看 ${esc(portEndpoint(row))} ${row.protocol.toUpperCase()} 详情">${row.port}</button><small class="port-mobile-address">${esc(row.address)}<span class="port-scope ${row.scope}">${portScopes[row.scope]}</span></small></td><td><span class="port-protocol ${row.protocol}">${row.protocol.toUpperCase()}</span></td><td><code>${esc(row.address)}</code><small class="port-meta">${row.family}</small></td><td><span class="port-scope ${row.scope}">${portScopes[row.scope]}</span></td><td><div class="port-service">${icon(row.containers.length?'box':'process')}<span><strong>${esc(portService(row))}</strong><small>${esc(row.containers.length?row.containers.map(c=>`${c.image} → ${c.target_port}/${c.protocol}`).join(' / '):row.processes.map(p=>`${p.name} · ${p.user}`).join(' / ') || '系统未提供进程归属')}</small></span></div></td><td>${row.processes.length?row.processes.map(p=>p.pid).join(', '):'—'}</td><td><span class="status-badge"><span class="dot"></span>${portStatuses[row.status]}</span></td><td><button class="button button-text" data-port-detail="${esc(row.id)}" aria-label="查看 ${esc(portEndpoint(row))} ${row.protocol.toUpperCase()} 详情">详情 ${icon('arrow')}</button></td></tr>`).join(''):`<tr><td colspan="8"><div class="empty-state">${data.loading?'正在读取监听端口…':data.error?'暂时无法读取端口':items.length?'没有匹配的端口':'当前未发现监听或发布的端口'}</div></td></tr>`;
  $('#port-results-count').textContent=`显示 ${rows.length} / ${items.length} 项 · 按地址、端口和协议区分`;
}

function handlePortClick(event) {
  const filter=event.target.closest('[data-port-filter]');
  if(filter){state.portFilter=filter.dataset.portFilter;$$('[data-port-filter]').forEach(button=>{button.classList.toggle('active',button.dataset.portFilter===state.portFilter);button.setAttribute('aria-pressed',button.dataset.portFilter===state.portFilter);});updatePorts();return true;}
  if(event.target.closest('[data-port-sort]')){state.portDirection*=-1;updatePorts();return true;}
  const detail=event.target.closest('[data-port-detail]');
  if(detail){openPort(detail.dataset.portDetail);return true;}
  return false;
}

function bindPorts() {
  $('#port-search').oninput=event=>{state.portSearch=event.target.value;updatePorts();};
  $('#port-scope').onchange=event=>{state.portScope=event.target.value;updatePorts();};
}

function openPort(id) {
  const row=portData().items.find(r=>r.id===id);if(!row)return;
  const pairs=[['端口',row.port],['协议 / 地址类型',`${row.protocol.toUpperCase()} / ${row.family}`],['绑定地址',portEndpoint(row)],['绑定范围',portScopes[row.scope]],['状态',portStatuses[row.status]],['宿主机采集时间',portData().updated_at?clock(portData().updated_at):'未完成']];
  $('#detail-content').innerHTML=`<div class="detail-header"><div><h2>${row.protocol.toUpperCase()} · ${row.port}</h2><p>${esc(portService(row))}</p></div><button class="icon-button" id="detail-close" aria-label="关闭端口详情">${icon('close')}</button></div><div class="detail-body port-detail"><dl>${pairs.map(([key,value])=>`<div><dt>${key}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl><h3>关联进程</h3>${row.processes.length?row.processes.map(p=>`<dl>${[['进程 / PID',`${p.name} / ${p.pid}`],['运行用户',p.user],['systemd 服务',p.service || '未识别 / 非 systemd 服务'],['可执行文件',p.executable || '未提供']].map(([key,value])=>`<div><dt>${key}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>${p.limited?'<p class="detail-note">进程已退出或读取权限不足，归属信息可能不完整。</p>':''}`).join(''):`<p class="detail-note">${row.source==='docker'?'此项来自 Docker 发布配置，NAT 转发可能没有宿主机监听进程。':'系统未提供 PID；可能因读取权限不足或进程已退出。'}</p>`}${row.containers.length?`<h3>Docker 映射</h3><p class="port-meta">映射采集时间：${portData().docker_updated_at?clock(portData().docker_updated_at):'未完成'}</p>${row.containers.map(c=>`<dl>${[['容器',c.name],['镜像',c.image],['转发',`${portEndpoint(row)} → ${c.target_port}/${c.protocol}`]].map(([key,value])=>`<div><dt>${key}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl><button class="button button-text" data-port-container="${esc(c.id)}">查看容器详情 ${icon('arrow')}</button>`).join('')}`:''}<div class="detail-note">这是打开详情时的快照。${row.scope==='local'?'此地址仅绑定本机回环接口。':row.scope==='all'?'此地址绑定同一地址族的全部网卡。':'此服务绑定指定网卡地址。'}是否能从外部访问，需要结合防火墙和路由判断。</div><div class="dialog-form-actions"><button class="button" id="port-copy-address">复制绑定地址</button></div></div>`;
  $('#detail-close').onclick=()=>$('#detail-dialog').close();
  $('#port-copy-address').onclick=()=>copyStoragePath(portEndpoint(row),'绑定地址已复制');
  $$('[data-port-container]').forEach(button=>button.onclick=()=>openContainer(button.dataset.portContainer));
  if(!$('#detail-dialog').open)$('#detail-dialog').showModal();
}
