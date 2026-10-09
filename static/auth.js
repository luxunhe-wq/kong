'use strict';

let authStatus = {initialized:false, registration_enabled:true};
let managedUsers = [];
let userSearch = '';

async function apiRequest(path, {method='GET', body} = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(()=>controller.abort(),12000);
  try {
    const headers = {};
    if(method!=='GET') {
      headers['Content-Type']='application/json';
      if(state.csrf) headers['X-CSRF-Token']=state.csrf;
    }
    const response = await fetch(path,{method,headers,credentials:'same-origin',cache:'no-store',signal:controller.signal,body:method==='GET'?undefined:JSON.stringify(body||{})});
    const result = await response.json();
    if(!response.ok) {
      if(response.status===401 && state.user) handleSessionExpired();
      throw new Error(result.error || '请求失败，请稍后重试');
    }
    return result;
  } catch(error) {
    if(error.name==='AbortError' || error instanceof TypeError) throw new Error('连接超时或网络不可用，请重试');
    throw error;
  } finally { clearTimeout(timeout); }
}

function clearSession() {
  state.authGeneration++;
  cancelMetricsRequest();
  clearTimeout(state.timer);
  state.user=null;state.csrf=null;state.data=null;state.fetching=false;state.failed=false;
  state.authBusy=false;
  resetStorageView();
  state.dockerSearch='';state.portSearch='';state.portFilter='all';state.portScope='any';state.portDirection=1;state.settingsTab='preferences';
  managedUsers=[];userSearch='';
  document.body.classList.remove('authenticated');
  $('#page-content').innerHTML='';$('#page-content').hidden=true;
  $('#connection-error').hidden=true;
  $('#loading').hidden=false;
  $('#loading').innerHTML='<span class="loading-spinner"></span><strong>正在连接服务器</strong><p>读取真实的资源与容器数据…</p>';
  $('#global-search').value='';$('#search-results').innerHTML='';$('#detail-content').innerHTML='';
  $$('dialog').forEach(dialog=>dialog.close());closeSidebar();
  $('#notification-popover').hidden=true;$('#notification-popover').innerHTML='';
}
function handleSessionExpired() {
  clearSession();authStatus.initialized=true;renderAuth('login','登录已过期或账号权限已变化，请重新登录。');
}
function activateSession(result) {
  state.user=result.user;state.csrf=result.csrf_token;
  authStatus.initialized=true;
  document.body.classList.add('authenticated');
  $('#auth-panel').innerHTML='';
  $('#profile-username').textContent=state.user.username;
  $('#profile-role').textContent=state.user.role==='admin'?'Administrator':'Read-only member';
  $$('[data-user-avatar]').forEach(el=>el.textContent=state.user.username[0].toUpperCase());
  $('#auth-screen').setAttribute('aria-hidden','true');
  renderPage();scheduleRefresh();fetchData();
}
function authFields(mode) {
  return `<label class="form-label" for="auth-username">账号</label><input class="form-input" id="auth-username" name="username" type="text" required minlength="3" maxlength="32" pattern="[A-Za-z0-9_][A-Za-z0-9_.\-]{2,31}" autocomplete="username" autocapitalize="none" spellcheck="false" placeholder="例如 kong_admin"><label class="form-label" for="auth-password">${mode==='login'?'密码':'设置密码'}</label><div class="password-field"><input class="form-input" id="auth-password" name="password" type="password" required ${mode==='login'?'':'minlength="10"'} maxlength="128" autocomplete="${mode==='login'?'current-password':'new-password'}" placeholder="${mode==='login'?'输入你的密码':'至少 10 个字符'}"><button class="password-visibility" type="button" aria-label="显示密码">显示</button></div>${mode==='login'?'':`<label class="form-label" for="auth-confirm">确认密码</label><input class="form-input" id="auth-confirm" type="password" required minlength="10" maxlength="128" autocomplete="new-password" placeholder="再次输入密码">`}`;
}
function renderAuth(mode='login', notice='') {
  $('#auth-screen').removeAttribute('aria-hidden');
  const copy={setup:{eyebrow:'WELCOME TO KONG',title:'从这里开始',description:'第一次见面。创建你的管理员账号，开启服务器监控。',button:'创建管理员并进入',note:'此账号拥有用户管理权限，初始化完成后入口将自动关闭。'},login:{eyebrow:'WELCOME BACK',title:'欢迎回来',description:'登录你的工作空间，看看服务器今天的状态。',button:'登录工作空间',note:'登录状态有效期为 12 小时。'},register:{eyebrow:'JOIN THE WORKSPACE',title:'加入工作空间',description:'创建账号，管理员审核通过后即可查看服务器监控。',button:'提交注册申请',note:'注册账号默认拥有监控查看权限，需要管理员审核。'}}[mode];
  $('#auth-panel').innerHTML=`<div class="auth-form-eyebrow">${copy.eyebrow}</div><h2>${copy.title}<span>.</span></h2><p class="auth-description">${copy.description}</p>${notice?`<div class="form-message info">${esc(notice)}</div>`:''}<form id="auth-form" data-mode="${mode}">${authFields(mode)}<div class="form-message error" id="auth-error" role="alert" hidden></div><button class="button button-primary auth-submit" type="submit">${copy.button}${icon('arrow')}</button></form><p class="auth-form-note">${copy.note}</p>${mode==='setup'?'':`<div class="auth-switch">${mode==='register'?'已有账号？':authStatus.registration_enabled?'还没有账号？':'注册已由管理员关闭'}${mode==='register'?'<button type="button" data-auth-mode="login">返回登录</button>':authStatus.registration_enabled?'<button type="button" data-auth-mode="register">申请加入</button>':''}</div>`}`;
  $('#auth-form').onsubmit=async event=>{
    event.preventDefault();const form=event.currentTarget;
    const password=$('#auth-password').value, confirm=$('#auth-confirm');
    $('#auth-error').hidden=true;
    if(confirm && confirm.value!==password){$('#auth-error').textContent='两次输入的密码不一致';$('#auth-error').hidden=false;confirm.focus();return;}
    const button=$('button[type="submit"]',form);button.disabled=true;
    try {
      const result=await apiRequest(`/api/auth/${mode}`,{method:'POST',body:{username:$('#auth-username').value.trim(),password}});
      if(mode==='register')renderAuth('login',result.message);
      else activateSession(result);
    } catch(error) {
      if($('#auth-error')){$('#auth-error').textContent=error.message;$('#auth-error').hidden=false;}
      button.disabled=false;
      if(mode==='setup' && error.message.includes('已初始化'))checkSession();
    }
  };
  $$('[data-auth-mode]').forEach(button=>button.onclick=()=>renderAuth(button.dataset.authMode));
  $('.password-visibility').onclick=event=>{const input=$('#auth-password');const visible=input.type==='password';input.type=visible?'text':'password';event.currentTarget.textContent=visible?'隐藏':'显示';event.currentTarget.setAttribute('aria-label',visible?'隐藏密码':'显示密码');};
}
async function checkSession() {
  try {
    authStatus=await apiRequest('/api/auth/status');
    if(authStatus.user)activateSession(authStatus);
    else renderAuth(authStatus.initialized?'login':'setup');
  } catch(error) {
    $('#auth-panel').innerHTML=`<div class="auth-form-eyebrow">CONNECTION</div><h2>暂时无法连接<span>.</span></h2><p class="auth-description">${esc(error.message)}</p><button class="button button-primary auth-submit" id="auth-retry">重新连接 ${icon('refresh')}</button>`;
    $('#auth-retry').onclick=checkSession;
  }
}

function accountSettingsLayout(preferencesPanel) {
  if(state.user.role!=='admin' && state.settingsTab==='users')state.settingsTab='preferences';
  const tabs=[['preferences','偏好设置'],['account','账户安全'],...(state.user.role==='admin'?[['users','用户管理']]:[])];
  const passwordForm=`<section class="panel settings-panel"><div class="settings-section"><h3>当前账户</h3><p>管理你的登录信息与账户密码。</p><div class="account-identity"><span class="avatar">${esc(state.user.username[0].toUpperCase())}</span><div><strong>${esc(state.user.username)}</strong><span>${state.user.role==='admin'?'管理员 · 可以管理用户与访问权限':'成员 · 只读查看服务器资源'}</span></div><button class="button" data-account-logout>退出登录</button></div></div><div class="settings-section"><h3>修改密码</h3><p>修改后其他设备的登录状态会失效，当前设备保持登录。</p><form id="password-change-form" class="account-form"><label class="form-label" for="current-password">当前密码</label><input class="form-input" id="current-password" type="password" autocomplete="current-password" required maxlength="128"><label class="form-label" for="new-password">新密码</label><input class="form-input" id="new-password" type="password" autocomplete="new-password" required minlength="10" maxlength="128" placeholder="至少 10 个字符"><label class="form-label" for="confirm-new-password">确认新密码</label><input class="form-input" id="confirm-new-password" type="password" autocomplete="new-password" required minlength="10" maxlength="128"><div class="form-message error" id="password-error" role="alert" hidden></div><button class="button button-primary" type="submit">保存新密码</button></form></div></section>`;
  const userManager=state.user.role==='admin'?`<section class="panel"><div class="panel-heading"><div class="panel-header-copy"><div class="panel-title"><h2>用户与访问权限</h2><span class="tag" id="users-count">—</span></div><p class="panel-subtitle">审核注册申请，管理谁可以访问这台服务器。</p></div><button class="button button-primary" data-user-create>创建用户 ${icon('arrow')}</button></div><div class="user-registration-row"><div class="setting-label"><strong>开放注册申请</strong><span>新用户提交申请后，仍需要管理员审核才能登录。</span></div><button class="toggle" id="registration-toggle" role="switch" aria-label="开放注册申请" aria-checked="false" disabled></button></div><div class="toolbar"><label class="filter-search">${icon('search')}<input id="user-search" placeholder="搜索账号或角色" aria-label="搜索用户" value="${esc(userSearch)}"></label><span class="toolbar-note" id="pending-users-count">加载用户中…</span></div><div class="form-message error user-manager-error" id="user-manager-error" role="alert" hidden></div><div class="table-scroll"><table class="user-table"><thead><tr><th>账号</th><th>权限</th><th>状态</th><th>创建时间</th><th>最近登录</th><th>操作</th></tr></thead><tbody id="user-table-body"><tr><td colspan="6"><div class="empty-state">正在读取用户…</div></td></tr></tbody></table></div><div class="table-footer"><span>管理员拥有用户管理权限，成员只读查看监控。</span><span>最多 500 个用户</span></div></section>`:'';
  return `<div class="settings-tabs" role="tablist" aria-label="设置分类">${tabs.map(([key,label])=>`<button role="tab" aria-selected="${state.settingsTab===key}" data-settings-tab="${key}" class="${state.settingsTab===key?'active':''}">${label}</button>`).join('')}</div><div data-settings-panel="preferences" ${state.settingsTab==='preferences'?'':'hidden'}>${preferencesPanel}</div><div data-settings-panel="account" ${state.settingsTab==='account'?'':'hidden'}>${passwordForm}</div>${userManager?`<div data-settings-panel="users" ${state.settingsTab==='users'?'':'hidden'}>${userManager}</div>`:''}`;
}
function bindAccountSettings() {
  $('#password-change-form').onsubmit=async event=>{
    event.preventDefault();$('#password-error').hidden=true;
    const password=$('#new-password').value;
    if(password!==$('#confirm-new-password').value){$('#password-error').textContent='两次输入的新密码不一致';$('#password-error').hidden=false;return;}
    const button=$('button[type="submit"]',event.currentTarget);button.disabled=true;
    state.authGeneration++;
    state.authBusy=true;
    cancelMetricsRequest();
    clearTimeout(state.timer);
    try {
      const result=await apiRequest('/api/auth/password',{method:'POST',body:{current_password:$('#current-password').value,new_password:password}});
      state.csrf=result.csrf_token;state.user=result.user;
      $('#password-change-form').reset();toast('密码已修改，其他设备需重新登录');
    } catch(error) {if($('#password-error')){$('#password-error').textContent=error.message;$('#password-error').hidden=false;}}
    finally {button.disabled=false;state.authBusy=false;if(state.user){scheduleRefresh();fetchData();}}
  };
  if(state.user.role==='admin') {
    $('#user-search').oninput=event=>{userSearch=event.target.value;renderUsers();};
    loadUsers();
  }
}
async function loadUsers() {
  try {
    const result=await apiRequest('/api/admin/users');
    if(!$('#user-table-body'))return;
    managedUsers=result.users;authStatus.registration_enabled=result.registration_enabled;
    const toggle=$('#registration-toggle');toggle.disabled=false;toggle.setAttribute('aria-checked',result.registration_enabled);
    $('#user-manager-error').hidden=true;renderUsers();
  } catch(error) {showUserError(error.message);}
}
function showUserError(message) {const target=$('#user-manager-error');if(target){target.textContent=message;target.hidden=false;}}
function dateLabel(timestamp) {return timestamp?new Date(timestamp*1000).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'尚未登录';}
function renderUsers() {
  if(!$('#user-table-body')||!state.user)return;
  const query=userSearch.toLowerCase();
  const users=managedUsers.filter(user=>`${user.username} ${user.role} ${user.role==='admin'?'管理员':'成员'}`.toLowerCase().includes(query));
  $('#users-count').textContent=`${managedUsers.length} 人`;
  $('#pending-users-count').textContent=`${managedUsers.filter(u=>u.status==='pending').length} 个账号待审核`;
  $('#user-table-body').innerHTML=users.length?users.map(user=>`<tr><td><div class="user-identity"><span class="avatar">${esc(user.username[0].toUpperCase())}</span><strong>${esc(user.username)}</strong>${user.id===state.user.id?'<span class="tag">你</span>':''}</div></td><td><span class="role-badge ${user.role==='admin'?'admin':''}">${user.role==='admin'?'管理员':'成员'}</span></td><td><span class="status-badge ${user.status==='pending'?'warning':user.status==='disabled'?'stopped':''}"><span class="dot"></span>${{active:'正常',pending:'待审核',disabled:'已停用'}[user.status]}</span></td><td>${dateLabel(user.created_at)}</td><td>${dateLabel(user.last_login)}</td><td><div class="user-actions">${user.status==='pending'?`<button class="button button-text" data-user-approve="${user.id}">通过</button>`:''}<button class="button button-text" data-user-edit="${user.id}">管理</button>${user.id===state.user.id?'':`<button class="button button-text danger-text" data-user-delete="${user.id}">删除</button>`}</div></td></tr>`).join(''):'<tr><td colspan="6"><div class="empty-state">没有匹配的用户</div></td></tr>';
}
function handleAccountClick(event) {
  const tab=event.target.closest('[data-settings-tab]');
  if(tab){state.settingsTab=tab.dataset.settingsTab;$$('[data-settings-tab]').forEach(button=>{button.classList.toggle('active',button.dataset.settingsTab===state.settingsTab);button.setAttribute('aria-selected',button.dataset.settingsTab===state.settingsTab);});$$('[data-settings-panel]').forEach(panel=>panel.hidden=panel.dataset.settingsPanel!==state.settingsTab);return true;}
  if(event.target.closest('[data-account-logout]')){logout();return true;}
  if(event.target.closest('[data-user-create]')){openUserForm();return true;}
  const edit=event.target.closest('[data-user-edit]');if(edit){openUserForm(Number(edit.dataset.userEdit));return true;}
  const remove=event.target.closest('[data-user-delete]');if(remove){openDeleteUser(Number(remove.dataset.userDelete));return true;}
  const approve=event.target.closest('[data-user-approve]');
  if(approve){approve.disabled=true;apiRequest(`/api/admin/users/${approve.dataset.userApprove}`,{method:'PATCH',body:{status:'active'}}).then(()=>{toast('账号已审核通过');return loadUsers();}).catch(error=>{showUserError(error.message);approve.disabled=false;});return true;}
  const toggle=event.target.closest('#registration-toggle');
  if(toggle){toggle.disabled=true;apiRequest('/api/admin/settings',{method:'PATCH',body:{registration_enabled:toggle.getAttribute('aria-checked')!=='true'}}).then(result=>{authStatus.registration_enabled=result.registration_enabled;toggle.setAttribute('aria-checked',result.registration_enabled);toast(result.registration_enabled?'已开放注册申请':'已关闭注册申请');}).catch(error=>showUserError(error.message)).finally(()=>toggle.disabled=false);return true;}
  return false;
}
function openUserForm(id) {
  const user=id?managedUsers.find(u=>u.id===id):null;if(id&&!user)return;
  const self=user?.id===state.user.id;
  $('#detail-content').innerHTML=`<div class="detail-header"><div><h2>${user?'管理用户':'创建用户'}</h2><p>${user?esc(user.username):'由管理员创建的账号可立即登录。'}</p></div><button class="icon-button" id="detail-close" aria-label="关闭用户管理">${icon('close')}</button></div><div class="detail-body"><form id="user-form">${user?'':`<label class="form-label" for="new-user-username">账号</label><input class="form-input" id="new-user-username" required minlength="3" maxlength="32" pattern="[A-Za-z0-9_][A-Za-z0-9_.\-]{2,31}" autocomplete="off" autocapitalize="none" spellcheck="false">`}<label class="form-label" for="user-role">权限</label><select class="form-input" id="user-role" ${self?'disabled':''}><option value="viewer" ${user?.role==='viewer'?'selected':''}>成员 · 查看监控</option><option value="admin" ${user?.role==='admin'?'selected':''}>管理员 · 查看监控并管理用户</option></select>${user?`<label class="form-label" for="user-status">账号状态</label><select class="form-input" id="user-status" ${self?'disabled':''}>${[['active','正常 · 允许登录'],['pending','待审核 · 暂不允许登录'],['disabled','已停用 · 禁止登录']].map(([key,label])=>`<option value="${key}" ${user.status===key?'selected':''}>${label}</option>`).join('')}</select>`:''}${self?'<div class="detail-note">请在账户安全中修改自己的密码。当前登录的管理员不能被停用或降级。</div>':`<label class="form-label" for="user-password">${user?'重置密码（选填）':'登录密码'}</label><input class="form-input" id="user-password" type="password" minlength="10" maxlength="128" autocomplete="new-password" ${user?'':'required'} placeholder="${user?'留空则保留原密码':'至少 10 个字符'}"><label class="form-label" for="user-password-confirm">确认密码</label><input class="form-input" id="user-password-confirm" type="password" maxlength="128" autocomplete="new-password" ${user?'':'required'}><div class="detail-note">重置密码、修改角色或停用账号，会使该用户已有登录状态失效。</div>`}<div class="form-message error" id="user-form-error" role="alert" hidden></div><div class="dialog-form-actions"><button type="button" class="button" id="user-form-cancel">取消</button><button type="submit" class="button button-primary">${user?'保存修改':'创建用户'}</button></div></form></div>`;
  $('#detail-close').onclick=$('#user-form-cancel').onclick=()=>$('#detail-dialog').close();
  $('#user-form').onsubmit=async event=>{
    event.preventDefault();$('#user-form-error').hidden=true;
    const password=$('#user-password')?.value;
    if(password && password!==$('#user-password-confirm').value){$('#user-form-error').textContent='两次输入的密码不一致';$('#user-form-error').hidden=false;return;}
    const body={role:$('#user-role').value};
    if(user){body.status=$('#user-status').value;if(password)body.password=password;}
    else {body.username=$('#new-user-username').value.trim();body.password=password;}
    const button=$('button[type="submit"]',event.currentTarget);button.disabled=true;
    try {await apiRequest(user?`/api/admin/users/${id}`:'/api/admin/users',{method:user?'PATCH':'POST',body});$('#detail-dialog').close();toast(user?'用户已更新':'用户已创建');await loadUsers();}
    catch(error){if($('#user-form-error')){$('#user-form-error').textContent=error.message;$('#user-form-error').hidden=false;}}
    finally {button.disabled=false;}
  };
  $('#detail-dialog').showModal();
}
function openDeleteUser(id) {
  const user=managedUsers.find(u=>u.id===id);if(!user)return;
  $('#detail-content').innerHTML=`<div class="detail-header"><div><h2>删除用户</h2><p>${esc(user.username)}</p></div><button class="icon-button" id="detail-close" aria-label="关闭删除确认">${icon('close')}</button></div><div class="detail-body"><p class="delete-explanation">删除后，<strong>${esc(user.username)}</strong> 将无法登录，已有会话也会失效。此操作不能撤销。</p><div class="form-message error" id="delete-user-error" role="alert" hidden></div><div class="dialog-form-actions"><button class="button" id="delete-user-cancel">取消</button><button class="button button-danger" id="delete-user-confirm">确认删除</button></div></div>`;
  $('#detail-close').onclick=$('#delete-user-cancel').onclick=()=>$('#detail-dialog').close();
  $('#delete-user-confirm').onclick=async event=>{const button=event.currentTarget;button.disabled=true;try{await apiRequest(`/api/admin/users/${id}`,{method:'DELETE'});$('#detail-dialog').close();toast('用户已删除');await loadUsers();}catch(error){if($('#delete-user-error')){$('#delete-user-error').textContent=error.message;$('#delete-user-error').hidden=false;}}finally{button.disabled=false;}};
  $('#detail-dialog').showModal();
}
async function logout() {
  try {await apiRequest('/api/auth/logout',{method:'POST'});clearSession();renderAuth('login');}
  catch(error){toast(error.message);}
}
$('#logout-button').onclick=logout;
navigate();
checkSession();
