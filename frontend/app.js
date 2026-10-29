const $ = id => document.getElementById(id);
let token = localStorage.getItem('ac_token') || '',
    me = null,
    currentPage = 'dashboard',
    profiles = [],
    activeChat = null,
    directoryOffset = 0,
    directoryLimit = 30,
    adminUsersCache = [],
    adminUserFilter = 'all',
    adminUserSearchTerm = '';

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (token) headers.Authorization = 'Bearer ' + token;
  let r = await fetch('/api' + path, { ...opts, headers });
  let d;
  try { d = await r.json(); } catch { d = {}; }
  if (!r.ok) throw Error(d.detail || 'Request failed (' + r.status + ')');
  return d;
}

function toast(msg) {
  const t = $('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.remove('hidden');
  setTimeout(() => t.classList.add('hidden'), 3200);
}

function initials(name) {
  return String(name || '?').split(/\s+/).slice(0, 2).map(x => x[0] || '').join('').toUpperCase();
}

function topbar() {
  const el = $('topright');
  if (!el) return;
  if (!me) {
    el.innerHTML = '<span class="muted small">AI-powered alumni networking</span>';
    return;
  }
  const roleBadge = me.role === 'admin'
    ? '<span class="pill purple">&#128737;\ufe0f Admin</span>'
    : me.role === 'alumni'
    ? `<span class="pill ${me.is_verified ? 'green' : 'amber'}">\u2b50 Alumni · ${me.is_verified ? 'Verified \u2713' : 'Pending Review'}</span>`
    : '<span class="pill blue">&#127891; Student</span>';

  el.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px">
      ${roleBadge}
      <span class="muted small userlabel">${esc(me.profile?.full_name || me.email)}</span>
      <button class="btn outline" data-action="logout">Log out</button>
    </div>
  `;
}

function authView(mode = 'login') {
  currentPage = 'auth';
  $('root').innerHTML = `
    <section class="authwrap">
      <div class="authhero">
        <div class="pill" style="background:#ffffff25;color:white;width:max-content">YOUR NETWORK. YOUR NEXT STEP.</div>
        <h1>Turn alumni connections into career opportunities.</h1>
        <p>Find the right mentor, learn from real experience, and build a stronger professional network with recommendations based on shared skills and fields of interest.</p>
        <div class="mini"><span>\u2726 Skill matching</span><span>\u25ce Verified alumni</span><span>\u2197 Mentorship</span></div>
      </div>
      <div class="authform">
        <div class="authbox">
          <h2>${mode === 'login' ? 'Welcome back' : 'Create your account'}</h2>
          <p class="muted">${mode === 'login' ? 'Sign in to continue your alumni journey.' : 'Join students and alumni building meaningful connections.'}</p>
          <div id="authnotice"></div>
          <form id="authform">
            ${mode === 'register' ? `
              <div class="field"><label>FULL NAME</label><input name="full_name" required minlength="2" placeholder="Your full name"></div>
              <div class="field"><label>ACCOUNT TYPE</label><select name="role"><option value="student">Student</option><option value="alumni">Alumni</option></select></div>
            ` : ''}
            <div class="field"><label>EMAIL ADDRESS</label><input name="email" type="email" required placeholder="you@example.com" autocomplete="email"></div>
            <div class="field"><label>PASSWORD</label><input name="password" type="password" required minlength="8" placeholder="At least 8 characters" autocomplete="${mode === 'login' ? 'current-password' : 'new-password'}"></div>
            ${mode === 'register' ? `
              <div class="field"><label>DEPARTMENT</label><input name="department" placeholder="e.g. AI & Data Science"></div>
              <div class="field"><label>SKILLS (comma-separated)</label><input name="skills" placeholder="Python, SQL, Machine Learning"></div>
            ` : ''}
            <button class="btn primary" style="width:100%;padding:13px" type="submit">${mode === 'login' ? 'Sign in' : 'Create account'} \u2192</button>
          </form>
          <p class="muted" style="margin-top:20px;text-align:center">
            ${mode === 'login' ? `New here? <span class="switch" data-auth-mode="register">Create an account</span>` : `Already registered? <span class="switch" data-auth-mode="login">Sign in</span>`}
          </p>
          <div class="footer">Your profile data stays in your institution's configured database.</div>
        </div>
      </div>
    </section>
  `;
  $('authform').onsubmit = async e => {
    e.preventDefault();
    const f = new FormData(e.target), body = Object.fromEntries(f.entries());
    if (mode === 'register') {
      body.skills = (body.skills || '').split(',').map(x => x.trim()).filter(Boolean);
      try {
        const result = await api('/auth/register', { method: 'POST', body: JSON.stringify(body) });
        token = result.access_token;
        localStorage.setItem('ac_token', token);
        me = result.user;
        topbar();
        currentPage = me.role === 'admin' ? 'admin' : 'dashboard';
        await renderApp();
        toast('Account created. Complete your profile for better recommendations.');
      } catch (err) {
        $('authnotice').innerHTML = `<div class="notice error">${esc(err.message)}</div>`;
      }
    } else {
      try {
        const result = await api('/auth/login', { method: 'POST', body: JSON.stringify(body) });
        token = result.access_token;
        localStorage.setItem('ac_token', token);
        me = result.user;
        topbar();
        currentPage = me.role === 'admin' ? 'admin' : 'dashboard';
        await renderApp();
        toast('Welcome back!');
      } catch (err) {
        $('authnotice').innerHTML = `<div class="notice error">${esc(err.message)}</div>`;
      }
    }
  };
}

function nav() {
  let items = [];
  let footerLabel = '';
  if (me?.role === 'admin') {
    items = [
      ['admin', '\u25a6', 'Operations Hub'],
      ['admin_verifications', '\u2713', 'Verification Queue'],
      ['admin_users', '&#128101;', 'User Management'],
      ['feed', '\u25a4', 'Community Moderation'],
      ['profile', '\u25c9', 'Admin Profile']
    ];
    footerLabel = 'AlumniConnect Operations<br><b>Administration Console</b>';
  } else if (me?.role === 'alumni') {
    items = [
      ['dashboard', '\u25a6', 'Alumni Hub'],
      ['mentorship', '\u2667', 'Mentorship Requests'],
      ['discover', '\u2315', 'Campus Directory'],
      ['feed', '\u25a4', 'Share Insights'],
      ['messages', '\u2709', 'Mentee Messages'],
      ['notifications', '\u2662', 'Notifications'],
      ['profile', '\u25c9', 'Alumni Profile']
    ];
    footerLabel = 'Alumni Mentorship Portal<br><b>Industry Guidance & Impact</b>';
  } else {
    items = [
      ['dashboard', '\u25a6', 'Dashboard'],
      ['discover', '\u2315', 'Discover alumni'],
      ['recommendations', '\u2726', 'Recommendations'],
      ['mentorship', '\u2667', 'Mentorship'],
      ['feed', '\u25a4', 'Community feed'],
      ['messages', '\u2709', 'Messages'],
      ['notifications', '\u2662', 'Notifications'],
      ['profile', '\u25c9', 'My profile']
    ];
    footerLabel = 'Smart Alumni Networking<br><b>Fuzzy + TF-IDF matching</b>';
  }

  return `
    <aside class="sidebar">
      ${items.map(([id, icon, label]) => `
        <button class="nav ${currentPage === id ? 'active' : ''}" data-go="${id}">
          <span class="ico">${icon}</span>${label}
        </button>
      `).join('')}
      <div class="sidefoot">${footerLabel}</div>
    </aside>
  `;
}

function layout(content) {
  $('root').innerHTML = `<div class="layout">${nav()}<main class="content">${content}</main></div>`;
}

function heading(title, sub, action = '') {
  return `<div class="heading"><div><h1>${title}</h1><p>${sub}</p></div>${action}</div>`;
}

function card(title, body, extra = '') {
  return `<section class="card"><div class="between"><h2>${title}</h2>${extra}</div>${body}</section>`;
}

function personCard(p, rec = false) {
  return `
    <div class="personline">
      <div class="avatar">${esc(initials(p.full_name))}</div>
      <div class="details">
        <div class="between">
          <strong>${esc(p.full_name)}</strong>
          ${rec ? `<span class="score">${p.match_score}%</span>` : ''}
        </div>
        <p>${esc([p.job_title, p.company, p.department].filter(Boolean).join(' · ') || p.role || 'Alumni network member')}</p>
        <div class="badgebar">
          ${(p.skills || []).slice(0, 4).map(s => `<span>${esc(s)}</span>`).join('')}
          ${p.is_verified ? '<span>\u2713 Verified</span>' : ''}
        </div>
        ${rec ? `<div class="reason">${(p.reasons || []).map(esc).join(' · ')}</div>` : ''}
      </div>
      <div style="display:grid;gap:6px">
        <button class="btn small primary" data-connect-id="${p.user_id}">Connect</button>
        ${p.role === 'alumni' ? `<button class="btn small outline" data-mentor-id="${p.user_id}">Mentor</button>` : ''}
      </div>
    </div>
  `;
}

async function renderApp() {
  topbar();
  if (!me) {
    authView();
    return;
  }
  layout('<div class="empty">Loading your workspace…</div>');
  try {
    if (me.role === 'admin' && currentPage === 'dashboard') {
      currentPage = 'admin';
    }
    if (currentPage === 'dashboard') {
      if (me.role === 'admin') await adminDashboard();
      else if (me.role === 'alumni') await alumniDashboard();
      else await studentDashboard();
    } else if (currentPage === 'admin' || currentPage === 'admin_dashboard') {
      await adminDashboard();
    } else if (currentPage === 'admin_verifications') {
      await adminVerificationsPage();
    } else if (currentPage === 'admin_users') {
      await adminUsersPage();
    } else if (currentPage === 'discover') {
      await discover();
    } else if (currentPage === 'recommendations') {
      await showRecommendations();
    } else if (currentPage === 'mentorship') {
      await mentorship();
    } else if (currentPage === 'feed') {
      await feed();
    } else if (currentPage === 'messages') {
      await messagesPage();
    } else if (currentPage === 'notifications') {
      await notificationsPage();
    } else if (currentPage === 'profile') {
      profilePage();
    } else {
      if (me.role === 'admin') await adminDashboard();
      else if (me.role === 'alumni') await alumniDashboard();
      else await studentDashboard();
    }
  } catch (e) {
    layout(
      heading('Something went wrong', 'The request could not be completed.') +
      `<div class="notice error">${esc(e.message)}</div><button class="btn" data-action="retry">Try again</button>`
    );
  }
}

async function go(page) {
  currentPage = page;
  await renderApp();
}

async function studentDashboard() {
  const [recs, posts, conns, mentors] = await Promise.all([
    api('/recommendations?limit=3').catch(() => []),
    api('/posts?limit=3').catch(() => []),
    api('/connections').catch(() => []),
    api('/mentorship').catch(() => [])
  ]);

  layout(
    heading(
      `Good ${new Date().getHours() < 12 ? 'morning' : new Date().getHours() < 18 ? 'afternoon' : 'evening'}, ${esc(me.profile?.full_name || 'there')} &#128075;`,
      'Build meaningful connections for your next career step.',
      `<button class="btn primary" data-go="profile">Complete profile \u2197</button>`
    ) +
    `<div class="grid" style="margin-bottom:16px">
      ${[
        ['\u2726', 'Alumni matches', recs.length, 'Based on skills + interests'],
        ['\u2194', 'Connections', conns.filter(c => c.status === 'accepted').length, 'Accepted connections'],
        ['\u2667', 'Mentorship', mentors.length, 'Requests sent or received']
      ].map(x => `
        <div class="card">
          <div class="metric-head"><div class="metric-icon">${x[0]}</div><span class="pill">Live</span></div>
          <div class="metric">${x[2]}</div>
          <div class="metric-label">${x[1]}</div>
          <div class="muted small" style="margin-top:7px">${x[3]}</div>
        </div>
      `).join('')}
    </div>
    <div class="two">
      ${card(
        'Recommended for you',
        recs.map(p => personCard(p, true)).join('') || '<div class="empty">Complete your profile to unlock recommendations.</div>',
        `<button class="btn small outline" data-go="recommendations">View all</button>`
      )}
      ${card(
        'Latest community posts',
        posts.map(p => `
          <div class="post">
            <div class="postmeta">${esc(p.author)} · ${esc(p.category)}</div>
            <h3>${esc(p.title)}</h3>
            <p>${esc(p.body.slice(0, 180))}${p.body.length > 180 ? '…' : ''}</p>
          </div>
        `).join('') || '<div class="empty">No posts yet.</div>',
        `<button class="btn small outline" data-go="feed">Open feed</button>`
      )}
    </div>`
  );
}

async function alumniDashboard() {
  const [mentors, conns, posts, studentProfiles] = await Promise.all([
    api('/mentorship').catch(() => []),
    api('/connections').catch(() => []),
    api('/posts?limit=5').catch(() => []),
    me.is_verified ? api('/profiles?role=student&limit=5').catch(() => []) : Promise.resolve([])
  ]);

  const incomingPending = mentors.filter(r => r.incoming && r.status === 'pending');
  const activeMentees = mentors.filter(r => r.status === 'accepted');
  const networkConns = conns.filter(c => c.status === 'accepted');

  const verificationNotice = !me.is_verified ? `
    <div class="notice" style="background:#fffbeb;border:1px solid #fde68a;color:#92400e;margin-bottom:18px">
      <div style="font-weight:750;font-size:14px;margin-bottom:3px">\u23f3 Verification in progress</div>
      Your alumni profile is awaiting campus administrator verification. Once verified, you will be featured across student recommendations and student discovery.
    </div>
  ` : '';

  const incomingMarkup = incomingPending.length ? incomingPending.map(r => `
    <div class="personline" style="align-items:flex-start">
      <div class="avatar">${esc(initials(r.participant?.full_name || 'Student'))}</div>
      <div class="details">
        <div class="between">
          <strong>${esc(r.participant?.full_name || 'Student')}</strong>
          <span class="pill amber">Pending your response</span>
        </div>
        <p style="font-weight:600;color:var(--ink);margin:4px 0">${esc(r.participant?.department || 'Student')}${r.participant?.graduation_year ? ' · Class of ' + esc(r.participant.graduation_year) : ''}</p>
        <div style="background:#f8fafc;border:1px solid var(--line);border-radius:9px;padding:8px 12px;margin:6px 0;font-size:13px">
          ${esc(r.message)}
        </div>
        <div class="muted small">${r.created_at ? 'Received ' + new Date(r.created_at).toLocaleDateString() : ''}</div>
      </div>
      <div style="display:grid;gap:6px">
        <button class="btn small primary" data-mentorship-id="${r.id}" data-status="accepted">Accept &amp; Mentor</button>
        <button class="btn small outline" data-mentorship-id="${r.id}" data-status="rejected">Decline</button>
      </div>
    </div>
  `).join('') : `
    <div class="empty">
      <div style="font-size:24px;margin-bottom:8px">&#127881;</div>
      <b>All caught up! No pending student requests.</b>
      <p class="muted small" style="margin-top:4px">When students in your field request mentorship or project feedback, their requests will appear right here.</p>
    </div>
  `;

  const studentsMarkup = studentProfiles.length ? studentProfiles.map(s => `
    <div class="personline">
      <div class="avatar">${esc(initials(s.full_name))}</div>
      <div class="details">
        <div class="between">
          <strong>${esc(s.full_name)}</strong>
          <span class="pill blue">Student</span>
        </div>
        <p>${esc([s.department, s.career_goal ? 'Goal: ' + s.career_goal : ''].filter(Boolean).join(' · '))}</p>
        <div class="badgebar">
          ${(s.skills || []).slice(0, 3).map(sk => `<span>${esc(sk)}</span>`).join('')}
        </div>
      </div>
      <div>
        <button class="btn small primary" data-connect-id="${s.user_id}">Connect</button>
      </div>
    </div>
  `).join('') : `
    <div class="empty">Explore the campus directory to discover students eager to learn.</div>
  `;

  layout(
    heading(
      `Welcome back, ${esc(me.profile?.full_name || 'Alumnus')} &#128075; · Alumni Mentorship Hub`,
      'Empower students with career guidance, share industry experience, and expand your professional network.',
      `<div style="display:flex;gap:8px">
        <button class="btn primary" data-go="feed">\uff0b Share industry insight</button>
        <button class="btn outline" data-go="mentorship">Review requests (${incomingPending.length})</button>
      </div>`
    ) +
    verificationNotice +
    `<div class="grid" style="margin-bottom:16px">
      ${[
        ['\u21c4', 'Pending Student Requests', incomingPending.length, incomingPending.length ? 'Awaiting your response' : 'Queue clear'],
        ['\u2667', 'Active Mentees', activeMentees.length, 'Students currently guiding'],
        ['\u2194', 'Alumni Connections', networkConns.length, 'Professional network'],
        ['\u2b50', 'Alumni Status', me.is_verified ? 'Verified \u2713' : 'Pending Review', me.profile?.company ? esc(me.profile.company) : 'Campus Alumni']
      ].map(x => `
        <div class="card">
          <div class="metric-head"><div class="metric-icon">${x[0]}</div><span class="pill ${x[1] === 'Pending Student Requests' && x[2] > 0 ? 'amber' : 'green'}">${x[1] === 'Pending Student Requests' && x[2] > 0 ? 'Action needed' : 'Active'}</span></div>
          <div class="metric">${x[2]}</div>
          <div class="metric-label">${x[1]}</div>
          <div class="muted small" style="margin-top:7px">${x[3]}</div>
        </div>
      `).join('')}
    </div>
    <div class="two">
      <div class="stack">
        ${card(
          'Incoming student mentorship requests',
          incomingMarkup,
          `<button class="btn small outline" data-go="mentorship">Open mentorship</button>`
        )}
        ${card(
          'Students seeking guidance in your domain',
          studentsMarkup,
          `<button class="btn small outline" data-go="discover">Campus directory</button>`
        )}
      </div>
      <div class="stack">
        <div class="card" style="background:linear-gradient(135deg,#1e3a8a,#2563eb);color:#fff">
          <h2 style="color:#fff;margin-top:0">Mentorship Guide for Alumni</h2>
          <p style="color:#dbeafe;font-size:13px;line-height:1.6">Students value practical guidance. You can make an immediate impact by:</p>
          <div style="display:grid;gap:8px;font-size:12px;margin:12px 0">
            <div style="background:#ffffff18;padding:8px 12px;border-radius:8px">\u2726 Reviewing student projects and GitHub portfolios</div>
            <div style="background:#ffffff18;padding:8px 12px;border-radius:8px">\u2726 Sharing interview expectations and placement prep tips</div>
            <div style="background:#ffffff18;padding:8px 12px;border-radius:8px">\u2726 Offering referral advice for entry-level engineering roles</div>
          </div>
          <button class="btn primary" style="background:#fff;color:#1e3a8a;width:100%;margin-top:4px" data-go="feed">Share a tip on Community Feed \u2192</button>
        </div>
        ${card(
          'Latest campus insights',
          posts.slice(0, 3).map(p => `
            <div class="post">
              <div class="postmeta">${esc(p.author)} · ${esc(p.category)}</div>
              <h3 style="font-size:14px">${esc(p.title)}</h3>
              <p style="font-size:13px">${esc(p.body.slice(0, 140))}${p.body.length > 140 ? '…' : ''}</p>
            </div>
          `).join('') || '<div class="empty">No community posts yet.</div>',
          `<button class="btn small outline" data-go="feed">Open feed</button>`
        )}
      </div>
    </div>`
  );
}

async function adminDashboard() {
  const [users, posts] = await Promise.all([
    api('/admin/users').catch(() => []),
    api('/posts?limit=5').catch(() => [])
  ]);
  adminUsersCache = users;

  const pendingAlumni = users.filter(u => u.role === 'alumni' && !u.is_verified);
  const verifiedAlumni = users.filter(u => u.role === 'alumni' && u.is_verified);
  const totalStudents = users.filter(u => u.role === 'student');
  const activeUsers = users.filter(u => u.is_active);

  const verificationQueueMarkup = pendingAlumni.length ? pendingAlumni.slice(0, 5).map(u => `
    <div class="personline" style="align-items:flex-start">
      <div class="avatar" style="background:#fef3c7;color:#b45309">${esc(initials(u.profile?.full_name || u.email))}</div>
      <div class="details">
        <div class="between">
          <strong>${esc(u.profile?.full_name || 'Alumnus')}</strong>
          <span class="pill amber">Pending review</span>
        </div>
        <p class="muted small">${esc(u.email)}</p>
        <p style="margin:4px 0;font-size:13px">
          ${esc([u.profile?.department, u.profile?.graduation_year ? 'Class of ' + u.profile.graduation_year : ''].filter(Boolean).join(' · '))}
        </p>
        <p style="margin:2px 0;font-weight:600;font-size:13px;color:#1e40af">
          ${esc([u.profile?.job_title, u.profile?.company].filter(Boolean).join(' at ') || 'Company details not provided')}
        </p>
      </div>
      <div style="display:flex;gap:6px;flex-direction:column">
        <button class="btn small primary" data-user-id="${u.id}" data-verified="true">\u2713 Verify &amp; Approve</button>
        <button class="btn small outline" data-user-id="${u.id}" data-active="false">Disable Account</button>
      </div>
    </div>
  `).join('') : `
    <div class="empty">
      <div style="font-size:24px;margin-bottom:6px">\u2713</div>
      <b>All alumni verification requests have been processed.</b>
      <p class="muted small" style="margin-top:4px">No pending review items in the queue.</p>
    </div>
  `;

  const recentUsersMarkup = users.slice(0, 6).map(u => `
    <tr class="table-row-hover">
      <td>
        <strong>${esc(u.profile?.full_name || 'User #' + u.id)}</strong>
        <div class="muted small">${esc(u.email)}</div>
      </td>
      <td>
        <span class="pill ${u.role === 'admin' ? 'purple' : u.role === 'alumni' ? 'green' : 'blue'}">
          ${u.role === 'admin' ? 'Admin' : u.role === 'alumni' ? 'Alumni' : 'Student'}
        </span>
      </td>
      <td>${esc(u.profile?.department || '—')}</td>
      <td>
        <span class="pill ${u.is_verified ? 'green' : 'amber'}">
          ${u.role === 'alumni' ? (u.is_verified ? 'Verified' : 'Pending') : '—'}
        </span>
      </td>
      <td>
        <span class="pill ${u.is_active ? 'green' : 'red'}">${u.is_active ? 'Active' : 'Disabled'}</span>
      </td>
      <td>
        ${u.role === 'alumni' ? `
          <button class="btn small ${u.is_verified ? 'outline' : 'primary'}" data-user-id="${u.id}" data-verified="${!u.is_verified}">
            ${u.is_verified ? 'Unverify' : 'Verify'}
          </button>
        ` : ''}
        ${u.id !== me.id ? `
          <button class="btn small" data-user-id="${u.id}" data-active="${!u.is_active}">
            ${u.is_active ? 'Disable' : 'Enable'}
          </button>
        ` : ''}
      </td>
    </tr>
  `).join('');

  layout(
    heading(
      'AlumniConnect Operations Console &#128737;\ufe0f',
      'Institutional oversight, alumni verification queue, and user account management.',
      `<div style="display:flex;gap:8px">
        <button class="btn primary" data-go="admin_verifications">Verification queue (${pendingAlumni.length})</button>
        <button class="btn outline" data-go="admin_users">All users (${users.length})</button>
      </div>`
    ) +
    `<div class="grid" style="margin-bottom:16px">
      ${[
        ['\u2713', 'Awaiting Verification', pendingAlumni.length, pendingAlumni.length ? 'Urgent: Review alumni credentials' : 'Queue clear', pendingAlumni.length > 0 ? 'amber' : 'green'],
        ['\u2b50', 'Verified Alumni', verifiedAlumni.length, 'Active industry mentors', 'green'],
        ['&#127891;', 'Enrolled Students', totalStudents.length, 'Active student network', 'blue'],
        ['&#128101;', 'Total Registered Users', users.length, `${activeUsers.length} accounts enabled`, 'green']
      ].map(x => `
        <div class="card">
          <div class="metric-head">
            <div class="metric-icon">${x[0]}</div>
            <span class="pill ${x[4]}">${x[1] === 'Awaiting Verification' && x[2] > 0 ? 'Action needed' : 'Normal'}</span>
          </div>
          <div class="metric">${x[2]}</div>
          <div class="metric-label">${x[1]}</div>
          <div class="muted small" style="margin-top:7px">${x[3]}</div>
        </div>
      `).join('')}
    </div>
    <div class="two">
      <div class="stack">
        ${card(
          'Alumni verification queue',
          verificationQueueMarkup,
          pendingAlumni.length > 5 ? `<button class="btn small outline" data-go="admin_verifications">View all (${pendingAlumni.length})</button>` : ''
        )}
        <div class="card">
          <div class="between" style="margin-bottom:12px">
            <h2>Recent account registrations</h2>
            <button class="btn small outline" data-go="admin_users">Full directory \u2192</button>
          </div>
          <div style="overflow-x:auto">
            <table class="table">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Role</th>
                  <th>Department</th>
                  <th>Verification</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                ${recentUsersMarkup}
              </tbody>
            </table>
          </div>
        </div>
      </div>
      <div class="stack">
        <div class="card">
          <h2>Platform Operations &amp; Health</h2>
          <div style="display:grid;gap:10px;margin-top:12px">
            <div class="between" style="padding:8px 0;border-bottom:1px solid var(--line)">
              <span>Database Connection</span>
              <span class="pill green">Healthy · Operational</span>
            </div>
            <div class="between" style="padding:8px 0;border-bottom:1px solid var(--line)">
              <span>Auth &amp; Access Control</span>
              <span class="pill green">Enforced · RBAC</span>
            </div>
            <div class="between" style="padding:8px 0;border-bottom:1px solid var(--line)">
              <span>Verification Policy</span>
              <span class="pill blue">Admin Approval Required</span>
            </div>
            <div class="between" style="padding:8px 0">
              <span>Community Feed</span>
              <span class="pill green">${posts.length} Posts Active</span>
            </div>
          </div>
          <button class="btn outline" style="width:100%;margin-top:14px" data-go="feed">Moderate Community Posts \u2192</button>
        </div>
        ${card(
          'Community feed monitor',
          posts.slice(0, 3).map(p => `
            <div class="post">
              <div class="postmeta">${esc(p.author)} · ${esc(p.category)} · ${p.created_at ? new Date(p.created_at).toLocaleDateString() : ''}</div>
              <h3 style="font-size:14px">${esc(p.title)}</h3>
              <p style="font-size:13px">${esc(p.body.slice(0, 140))}${p.body.length > 140 ? '…' : ''}</p>
            </div>
          `).join('') || '<div class="empty">No posts to display.</div>',
          `<button class="btn small outline" data-go="feed">Open feed</button>`
        )}
      </div>
    </div>`
  );
}

async function adminVerificationsPage() {
  const users = await api('/admin/users');
  adminUsersCache = users;
  const pending = users.filter(u => u.role === 'alumni' && !u.is_verified);
  const verified = users.filter(u => u.role === 'alumni' && u.is_verified);

  layout(
    heading(
      'Alumni Verification Queue \u2713',
      'Verify degree, graduation year, and professional credentials before alumni are recommended to students.',
      `<button class="btn outline" data-go="admin">\u2190 Back to Dashboard</button>`
    ) +
    `<div class="card" style="margin-bottom:16px">
      <div class="between">
        <div>
          <h2>Pending Review (${pending.length})</h2>
          <p class="muted small">These alumni accounts cannot be recommended to students until verified by an administrator.</p>
        </div>
      </div>
      <div style="margin-top:14px">
        ${pending.length ? pending.map(u => `
          <div class="personline" style="align-items:flex-start;padding:16px 0">
            <div class="avatar" style="background:#fef3c7;color:#b45309">${esc(initials(u.profile?.full_name || u.email))}</div>
            <div class="details">
              <div class="between">
                <strong style="font-size:16px">${esc(u.profile?.full_name || 'Alumnus')}</strong>
                <span class="pill amber">Pending Review</span>
              </div>
              <div class="muted small">${esc(u.email)}</div>
              <div style="margin:8px 0;font-size:13px">
                <b>Department:</b> ${esc(u.profile?.department || 'Not specified')} · 
                <b>Graduation Year:</b> ${esc(u.profile?.graduation_year || 'Not specified')}
              </div>
              <div style="margin:4px 0;font-size:13px;color:#1e40af">
                <b>Company &amp; Role:</b> ${esc([u.profile?.job_title, u.profile?.company].filter(Boolean).join(' at ') || 'Not specified')}
              </div>
              ${u.profile?.bio ? `<p style="margin:6px 0;color:var(--muted);font-size:13px">${esc(u.profile.bio)}</p>` : ''}
              <div class="badgebar">
                ${(u.profile?.skills || []).map(s => `<span>${esc(s)}</span>`).join('')}
              </div>
            </div>
            <div style="display:flex;gap:8px;flex-direction:column;flex-shrink:0">
              <button class="btn primary" data-user-id="${u.id}" data-verified="true">\u2713 Approve &amp; Verify</button>
              <button class="btn danger" data-user-id="${u.id}" data-active="false">Disable Account</button>
            </div>
          </div>
        `).join('') : '<div class="empty">\u2713 No alumni accounts currently awaiting verification. All alumni have been reviewed!</div>'}
      </div>
    </div>
    <div class="card">
      <div class="between">
        <h2>Previously Verified Alumni (${verified.length})</h2>
      </div>
      <div style="overflow-x:auto;margin-top:12px">
        <table class="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Email</th>
              <th>Department &amp; Batch</th>
              <th>Current Employment</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            ${verified.map(u => `
              <tr>
                <td><strong>${esc(u.profile?.full_name || 'Alumnus')}</strong></td>
                <td class="muted small">${esc(u.email)}</td>
                <td>${esc([u.profile?.department, u.profile?.graduation_year].filter(Boolean).join(' · '))}</td>
                <td>${esc([u.profile?.job_title, u.profile?.company].filter(Boolean).join(' · ') || '—')}</td>
                <td>
                  <button class="btn small outline" data-user-id="${u.id}" data-verified="false">Revoke Verification</button>
                </td>
              </tr>
            `).join('') || '<tr><td colspan="5" class="empty">No verified alumni yet.</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>`
  );
}

async function adminUsersPage() {
  const users = await api('/admin/users');
  adminUsersCache = users;
  renderAdminUsersTable();
}

function renderAdminUsersTable() {
  const q = (adminUserSearchTerm || '').toLowerCase();
  let list = adminUsersCache;
  if (adminUserFilter === 'student') list = list.filter(u => u.role === 'student');
  else if (adminUserFilter === 'alumni') list = list.filter(u => u.role === 'alumni');
  else if (adminUserFilter === 'pending') list = list.filter(u => u.role === 'alumni' && !u.is_verified);
  else if (adminUserFilter === 'admin') list = list.filter(u => u.role === 'admin');

  if (q) {
    list = list.filter(u => [u.email, u.role, u.profile?.full_name, u.profile?.department, u.profile?.company, u.profile?.job_title].join(' ').toLowerCase().includes(q));
  }

  layout(
    heading(
      'User Management & Directory &#128101;',
      'Manage student, alumni, and administrator access and verification status.',
      `<button class="btn outline" data-go="admin">\u2190 Back to Dashboard</button>`
    ) +
    `<div class="card">
      <div class="toolbar" style="align-items:center">
        <input class="search" id="adminUserSearchInput" placeholder="Search users by name, email, department, company…" value="${esc(adminUserSearchTerm)}">
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn small ${adminUserFilter === 'all' ? 'primary' : 'outline'}" data-role-filter="all">All (${adminUsersCache.length})</button>
          <button class="btn small ${adminUserFilter === 'student' ? 'primary' : 'outline'}" data-role-filter="student">Students (${adminUsersCache.filter(u => u.role === 'student').length})</button>
          <button class="btn small ${adminUserFilter === 'alumni' ? 'primary' : 'outline'}" data-role-filter="alumni">Alumni (${adminUsersCache.filter(u => u.role === 'alumni').length})</button>
          <button class="btn small ${adminUserFilter === 'pending' ? 'primary' : 'outline'}" data-role-filter="pending">Pending (${adminUsersCache.filter(u => u.role === 'alumni' && !u.is_verified).length})</button>
          <button class="btn small ${adminUserFilter === 'admin' ? 'primary' : 'outline'}" data-role-filter="admin">Admins (${adminUsersCache.filter(u => u.role === 'admin').length})</button>
        </div>
      </div>
      <div style="overflow-x:auto;margin-top:14px">
        <table class="table">
          <thead>
            <tr>
              <th>User</th>
              <th>Role</th>
              <th>Department &amp; Year</th>
              <th>Company / Role</th>
              <th>Verification</th>
              <th>Account Access</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            ${list.map(u => `
              <tr class="table-row-hover">
                <td>
                  <strong>${esc(u.profile?.full_name || 'User #' + u.id)}</strong>
                  <div class="muted small">${esc(u.email)}</div>
                </td>
                <td>
                  <span class="pill ${u.role === 'admin' ? 'purple' : u.role === 'alumni' ? 'green' : 'blue'}">
                    ${u.role === 'admin' ? 'Admin' : u.role === 'alumni' ? 'Alumni' : 'Student'}
                  </span>
                </td>
                <td>${esc([u.profile?.department, u.profile?.graduation_year].filter(Boolean).join(' · ') || '—')}</td>
                <td>${esc([u.profile?.job_title, u.profile?.company].filter(Boolean).join(' · ') || '—')}</td>
                <td>
                  <span class="pill ${u.is_verified ? 'green' : 'amber'}">
                    ${u.role === 'alumni' ? (u.is_verified ? 'Verified \u2713' : 'Pending Review') : '—'}
                  </span>
                </td>
                <td>
                  <span class="pill ${u.is_active ? 'green' : 'red'}">${u.is_active ? 'Active' : 'Disabled'}</span>
                </td>
                <td>
                  ${u.role === 'alumni' ? `
                    <button class="btn small ${u.is_verified ? 'outline' : 'primary'}" data-user-id="${u.id}" data-verified="${!u.is_verified}">
                      ${u.is_verified ? 'Unverify' : 'Verify'}
                    </button>
                  ` : ''}
                  ${u.id !== me.id ? `
                    <button class="btn small" data-user-id="${u.id}" data-active="${!u.is_active}">
                      ${u.is_active ? 'Disable' : 'Enable'}
                    </button>
                  ` : ''}
                </td>
              </tr>
            `).join('') || '<tr><td colspan="7" class="empty">No matching users found.</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>`
  );

  const searchInput = $('adminUserSearchInput');
  if (searchInput) {
    searchInput.oninput = e => {
      adminUserSearchTerm = e.target.value;
      renderAdminUsersTable();
    };
  }
}

async function discover() {
  const isAlumni = me?.role === 'alumni';
  const title = isAlumni ? 'Campus Directory & Students' : 'Discover alumni';
  const subtitle = isAlumni
    ? 'Explore students looking for guidance, and connect with fellow alumni.'
    : 'Search verified alumni and students in your network.';

  layout(
    heading(title, subtitle) +
    `<section class="card">
      <form id="directoryForm" class="toolbar">
        <input class="search" name="q" id="directoryQuery" placeholder="Name, company, role, interests…">
        <input class="search" name="department" id="directoryDepartment" placeholder="Department" style="max-width:220px">
        <input class="search" name="skill" id="directorySkill" placeholder="Skill" style="max-width:180px">
        <button class="btn primary">Search</button>
      </form>
      <div id="directory" aria-live="polite"></div>
      <div class="between" style="margin-top:14px">
        <button class="btn outline" id="directoryPrevious">Previous</button>
        <span class="muted small" id="directoryPage"></span>
        <button class="btn outline" id="directoryNext">Next</button>
      </div>
    </section>`
  );

  $('directoryForm').onsubmit = async e => {
    e.preventDefault();
    directoryOffset = 0;
    await loadDirectory();
  };
  $('directoryPrevious').onclick = async () => {
    directoryOffset = Math.max(0, directoryOffset - directoryLimit);
    await loadDirectory();
  };
  $('directoryNext').onclick = async () => {
    directoryOffset += directoryLimit;
    await loadDirectory();
  };
  await loadDirectory();
}

async function loadDirectory() {
  const params = new URLSearchParams({ limit: String(directoryLimit), offset: String(directoryOffset) });
  const fields = { q: 'directoryQuery', department: 'directoryDepartment', skill: 'directorySkill' };
  for (const [key, id] of Object.entries(fields)) {
    const value = $(id)?.value?.trim() || '';
    if (value) params.set(key, value);
  }
  const list = await api('/profiles?' + params.toString());
  profiles = list;
  $('directory').innerHTML = list.map(p => personCard(p)).join('') || '<div class="empty">No matching profiles. Try another search.</div>';
  $('directoryPrevious').disabled = directoryOffset === 0;
  $('directoryNext').disabled = list.length < directoryLimit;
  $('directoryPage').textContent = `Profiles ${directoryOffset + 1}–${directoryOffset + list.length}${list.length === directoryLimit ? ' · more may be available' : ''}`;
}

function filterProfiles() {
  const q = $('q')?.value?.toLowerCase() || '', d = $('dept')?.value || '';
  const found = profiles.filter(p => (!d || p.department === d) && [p.full_name, p.company, p.job_title, p.department, p.interests, ...p.skills].join(' ').toLowerCase().includes(q));
  $('directory').innerHTML = found.map(p => personCard(p)).join('') || '<div class="empty">No matching profiles.</div>';
}

async function showRecommendations() {
  const list = await api('/recommendations?limit=50');
  layout(
    heading('Recommendations', 'Matches use fuzzy skill and TF-IDF interest and career similarity.') +
    `<div class="notice">Score weights: skills 50%, interests 30%, career fit 15%, department 5%. Missing fields are ignored and remaining weights are scaled.</div>
    <div class="card">
      <div class="between"><h2>Best-fit connections</h2><span class="pill">Fuzzy + TF-IDF</span></div>
      ${list.map(p => personCard(p, true)).join('') || '<div class="empty">No other members yet. Add a second account to see matches.</div>'}
    </div>`
  );
}

async function connect(id) {
  try {
    await api('/connections', { method: 'POST', body: JSON.stringify({ recipient_id: id }) });
    toast('Connection request sent.');
    await renderApp();
  } catch (e) {
    toast(e.message);
  }
}

async function mentor(id) {
  const message = prompt('What would you like guidance on?');
  if (!message) return;
  try {
    await api('/mentorship', { method: 'POST', body: JSON.stringify({ alumni_id: id, message }) });
    toast('Mentorship request sent.');
    await renderApp();
  } catch (e) {
    toast(e.message);
  }
}

async function mentorship() {
  const [list, connections] = await Promise.all([
    api('/mentorship'),
    api('/connections')
  ]);

  const isAlumni = me?.role === 'alumni';

  const mentorshipTitle = isAlumni ? 'Student Mentorship Requests' : 'Mentorship & connections';
  const mentorshipSubtitle = isAlumni
    ? 'Review incoming guidance requests from students and manage your active mentees.'
    : 'Manage requests and grow your professional network.';

  layout(
    heading(mentorshipTitle, mentorshipSubtitle) +
    `<div class="two">
      ${card(
        isAlumni ? 'Incoming student requests' : 'Mentorship requests',
        list.map(r => `
          <div class="personline" style="align-items:flex-start">
            <div class="avatar">${esc(initials(r.participant?.full_name || 'User'))}</div>
            <div class="details">
              <div class="between">
                <strong>${esc(r.participant?.full_name || `Request #${r.id}`)}</strong>
                <span class="pill ${r.status === 'accepted' ? 'green' : r.status === 'pending' ? 'amber' : 'red'}">${esc(r.status)}</span>
              </div>
              <p style="font-weight:600;margin:3px 0;color:var(--ink)">
                ${isAlumni ? (r.incoming ? 'Incoming student request' : 'Sent request') : (r.incoming ? 'Incoming from' : 'Sent to')} ${esc(r.participant?.role === 'alumni' ? 'alumnus' : 'student')} · 
                ${esc([r.participant?.department, r.participant?.job_title, r.participant?.company].filter(Boolean).join(' · '))}
              </p>
              <div style="background:#f8fafc;border:1px solid var(--line);border-radius:9px;padding:8px 12px;margin:6px 0;font-size:13px">
                ${esc(r.message)}
              </div>
              <div class="muted small">${r.created_at ? new Date(r.created_at).toLocaleDateString() : ''}</div>
            </div>
            ${r.incoming && r.status === 'pending' ? `
              <div style="display:grid;gap:5px;flex-shrink:0">
                <button class="btn small primary" data-mentorship-id="${r.id}" data-status="accepted">Accept</button>
                <button class="btn small outline" data-mentorship-id="${r.id}" data-status="rejected">Decline</button>
              </div>
            ` : r.status === 'accepted' ? `
              <button class="btn small outline" data-go="messages">Message</button>
            ` : ''}
          </div>
        `).join('') || '<div class="empty">No mentorship requests yet.</div>'
      )}
      ${card(
        'Connection requests',
        connections.map(c => `
          <div class="personline">
            <div class="avatar">${esc(initials(c.participant?.full_name || 'User'))}</div>
            <div class="details">
              <div class="between">
                <strong>${esc(c.participant?.full_name || `Connection #${c.id}`)}</strong>
                <span class="pill ${c.status === 'accepted' ? 'green' : c.status === 'pending' ? 'amber' : 'red'}">${esc(c.status)}</span>
              </div>
              <p>${c.incoming ? 'Incoming connection request' : 'Connection request sent'} · ${esc([c.participant?.job_title, c.participant?.company].filter(Boolean).join(' · '))}</p>
            </div>
            ${c.incoming && c.status === 'pending' ? `
              <div style="display:grid;gap:5px;flex-shrink:0">
                <button class="btn small primary" data-connection-id="${c.id}" data-status="accepted">Accept</button>
                <button class="btn small outline" data-connection-id="${c.id}" data-status="rejected">Decline</button>
              </div>
            ` : ''}
          </div>
        `).join('') || '<div class="empty">No connections yet.</div>'
      )}
    </div>`
  );
}

async function respondMentor(id, status) {
  try {
    await api('/mentorship/' + id, { method: 'PATCH', body: JSON.stringify({ status }) });
    toast('Request ' + status + '.');
    await renderApp();
  } catch (e) {
    toast(e.message);
  }
}

async function respondConnection(id, status) {
  try {
    await api('/connections/' + id, { method: 'PATCH', body: JSON.stringify({ status }) });
    toast('Connection ' + status + '.');
    await renderApp();
  } catch (e) {
    toast(e.message);
  }
}

async function feed() {
  const posts = await api('/posts?limit=100');
  const isAlumni = me?.role === 'alumni';
  const isAdmin = me?.role === 'admin';

  const feedTitle = isAdmin ? 'Community feed & moderation' : isAlumni ? 'Industry insights & advice' : 'Community feed';
  const feedSubtitle = isAdmin
    ? 'Monitor community knowledge sharing, discussions, and member updates.'
    : isAlumni
    ? 'Share actionable career advice, interview tips, technology trends, and opportunities with students.'
    : 'Career advice, industry insights, and opportunities shared by your network.';

  const postMarkup = p => `
    <article class="post" style="border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0">
      <div class="postmeta">${esc(p.author)} · ${esc(p.category)} · ${p.created_at ? new Date(p.created_at).toLocaleDateString() : ''}</div>
      <h3>${esc(p.title)}</h3>
      <p>${esc(p.body)}</p>
      <div class="actions">
        <button class="btn small ${p.liked ? 'primary' : ''}" data-action="reaction" data-kind="liked" data-post-id="${p.id}" aria-pressed="${p.liked}">${p.liked ? '\u2665' : '\u2661'} Helpful · ${p.likes_count}</button>
        <button class="btn small ${p.bookmarked ? 'primary' : ''}" data-action="reaction" data-kind="bookmarked" data-post-id="${p.id}" aria-pressed="${p.bookmarked}">${p.bookmarked ? '\u25a3 Saved' : '\u2667 Save'}</button>
        <button class="btn small outline" data-action="comments" data-post-id="${p.id}" aria-expanded="false">Comments · ${p.comments_count}</button>
      </div>
      <div class="post-comments" id="comments-${p.id}"></div>
    </article>
  `;

  layout(
    heading(feedTitle, feedSubtitle) +
    `<div class="two">
      <section class="card">
        <h2>${isAlumni ? 'Share an industry insight or opportunity' : 'Share an update'}</h2>
        <form id="postform">
          <div class="field">
            <label>TITLE</label>
            <input name="title" required minlength="3" maxlength="240" placeholder="${isAlumni ? 'e.g. What we look for in software engineering interviews' : 'What would you like to share?'}">
          </div>
          <div class="field">
            <label>CATEGORY</label>
            <select name="category">
              <option>General</option>
              <option>Hiring Trends</option>
              <option>Skills &amp; Learning</option>
              <option>Technology</option>
              <option>Career Advice</option>
              <option>Opportunities</option>
            </select>
          </div>
          <div class="field">
            <label>POST</label>
            <textarea name="body" required minlength="5" maxlength="10000" placeholder="${isAlumni ? 'Explain what students should know, why it matters, and how to practice or apply…' : 'Share useful details with the community…'}"></textarea>
          </div>
          <button class="btn primary" type="submit">${isAlumni ? 'Publish insight' : 'Publish post'}</button>
        </form>
      </section>
      <section class="card">
        <h2>Recent community posts</h2>
        <div id="feedPosts">${posts.map(postMarkup).join('') || '<div class="empty">Be the first to share an update.</div>'}</div>
      </section>
    </div>`
  );

  $('postform').onsubmit = async e => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target));
    try {
      await api('/posts', { method: 'POST', body: JSON.stringify(body) });
      toast('Post published.');
      await feed();
    } catch (err) {
      toast(err.message);
    }
  };

  $('feedPosts').addEventListener('click', async e => {
    const button = e.target.closest('[data-action]');
    if (!button) return;
    const postId = Number(button.dataset.postId);
    try {
      if (button.dataset.action === 'reaction') {
        const kind = button.dataset.kind, enabled = button.getAttribute('aria-pressed') !== 'true';
        await api(`/posts/${postId}/reactions/${kind}`, { method: 'PUT', body: JSON.stringify({ enabled }) });
        await feed();
      } else {
        const panel = $(`comments-${postId}`);
        if (panel.innerHTML) {
          panel.innerHTML = '';
          button.setAttribute('aria-expanded', 'false');
        } else {
          await loadPostComments(postId);
          button.setAttribute('aria-expanded', 'true');
        }
      }
    } catch (err) {
      toast(err.message);
    }
  });

  $('feedPosts').addEventListener('submit', async e => {
    if (!e.target.matches('.post-comment-form')) return;
    e.preventDefault();
    const postId = Number(e.target.dataset.postId), body = new FormData(e.target).get('body');
    try {
      await api(`/posts/${postId}/comments`, { method: 'POST', body: JSON.stringify({ body }) });
      toast('Comment added.');
      await feed();
    } catch (err) {
      toast(err.message);
    }
  });
}

async function loadPostComments(postId) {
  const comments = await api(`/posts/${postId}/comments`);
  const panel = $(`comments-${postId}`);
  panel.innerHTML = `
    <div class="stack" style="margin-top:12px">
      ${comments.map(comment => `
        <div class="notice" style="margin:0">
          <strong>${esc(comment.author)}</strong><span class="muted small"> · ${esc(comment.role)}</span>
          <div>${esc(comment.body)}</div>
        </div>
      `).join('') || '<div class="muted small">No comments yet.</div>'}
      <form class="post-comment-form" data-post-id="${postId}" style="margin-top:8px">
        <div class="field">
          <label>ADD A COMMENT</label>
          <textarea name="body" required maxlength="3000" placeholder="Write a helpful comment…"></textarea>
        </div>
        <button class="btn small primary">Comment</button>
      </form>
    </div>
  `;
}

async function messagesPage() {
  const conns = (await api('/connections')).filter(c => c.status === 'accepted');
  const otherIds = [...new Set(conns.map(c => c.requester_id === me.id ? c.recipient_id : c.requester_id))];
  let people = [];
  for (const id of otherIds) {
    try {
      people.push(await api('/profiles/' + id));
    } catch {}
  }

  layout(
    heading('Messages', 'Private messages are enabled between accepted connections.') +
    `<div class="two">
      <div class="card">
        <h2>Your connections</h2>
        ${people.map(p => `
          <button class="personline chat-person" data-chat-id="${Number(p.user_id)}" data-chat-name="${esc(p.full_name)}" style="width:100%;text-align:left;background:white">
            <div class="avatar">${esc(initials(p.full_name))}</div>
            <div class="details">
              <strong>${esc(p.full_name)}</strong>
              <p>${esc(p.job_title || p.role)}</p>
            </div>
            <span>\u2192</span>
          </button>
        `).join('') || '<div class="empty">Accept a connection request to start messaging.</div>'}
      </div>
      <div class="card">
        <h2 id="chatTitle">Choose a connection</h2>
        <div id="chatBody" class="stack" style="max-height:350px;overflow:auto;margin:14px 0"></div>
        <form id="msgform" class="hidden">
          <div class="field">
            <textarea name="body" required placeholder="Write a message…" style="min-height:65px"></textarea>
          </div>
          <button class="btn primary">Send message</button>
        </form>
      </div>
    </div>`
  );

  document.querySelectorAll('.chat-person').forEach(button =>
    button.addEventListener('click', () => openChat(Number(button.dataset.chatId), button.dataset.chatName || 'Connection'))
  );
}

async function openChat(id, name) {
  activeChat = id;
  $('chatTitle').textContent = name;
  const form = $('msgform');
  form.classList.remove('hidden');
  form.onsubmit = async e => {
    e.preventDefault();
    const body = new FormData(form).get('body');
    try {
      await api('/messages', { method: 'POST', body: JSON.stringify({ recipient_id: id, body }) });
      form.reset();
      await loadChat(id);
    } catch (err) {
      toast(err.message);
    }
  };
  await loadChat(id);
}

async function loadChat(id) {
  const msgs = await api('/messages/' + id);
  $('chatBody').innerHTML = msgs.map(m => `
    <div style="max-width:85%;padding:10px 12px;border-radius:12px;background:${m.sender_id === me.id ? '#edf2ff' : '#f0f3f8'};align-self:${m.sender_id === me.id ? 'end' : 'start'}">
      <div>${esc(m.body)}</div>
      <div class="muted small">${m.created_at ? new Date(m.created_at).toLocaleString() : ''}</div>
    </div>
  `).join('') || '<div class="empty">Say hello and start the conversation.</div>';
}

async function notificationsPage() {
  const list = await api('/notifications');
  layout(
    heading('Notifications', 'Stay updated on connection requests, mentorship, and messages.') +
    `<div class="card">
      ${list.map(n => `
        <div class="personline">
          <div class="metric-icon">\u2662</div>
          <div class="details">
            <strong>${esc(n.message)}</strong>
            <p>${n.created_at ? new Date(n.created_at).toLocaleString() : ''}</p>
          </div>
          ${n.read ? '<span class="pill">Read</span>' : `<button class="btn small" data-notification-id="${n.id}">Mark read</button>`}
        </div>
      `).join('') || '<div class="empty">You are all caught up.</div>'}
    </div>`
  );
}

async function readNotification(id) {
  await api('/notifications/' + id + '/read', { method: 'PATCH' });
  notificationsPage();
}

function profilePage() {
  const p = me.profile || {};
  const isAlumni = me.role === 'alumni';
  const isAdmin = me.role === 'admin';

  const roleTitle = isAdmin ? 'Admin Profile & System Settings' : isAlumni ? 'Alumni Mentor Profile' : 'My profile';
  const roleSubtitle = isAdmin
    ? 'Administrator account details and institutional governance profile.'
    : isAlumni
    ? 'Highlight your professional background, industry experience, and mentoring topics for campus students.'
    : 'Matches use skills, interests, career goal, and department context.';

  const bannerMeta = isAdmin
    ? 'Platform Administrator · System Oversight'
    : isAlumni
    ? `${esc([p.job_title, p.company].filter(Boolean).join(' at ') || 'Alumni Mentor')} · Class of ${esc(p.graduation_year || 'N/A')}`
    : `${esc(p.department || 'Student')} · Class of ${esc(p.graduation_year || 'N/A')}`;

  const rolePill = isAdmin
    ? '<span class="pill purple">&#128737;\ufe0f System Administrator</span>'
    : isAlumni
    ? `<span class="pill ${me.is_verified ? 'green' : 'amber'}">${me.is_verified ? '\u2713 Verified Alumnus' : '\u23f3 Verification Pending'}</span>`
    : '<span class="pill blue">&#127891; Student</span>';

  layout(
    heading(roleTitle, roleSubtitle) +
    `<div class="card">
      <div class="profile-hero">
        <div class="avatar" style="width:58px;height:58px;font-size:20px">${esc(initials(p.full_name))}</div>
        <div>
          <h2 style="margin:0">${esc(p.full_name || 'Your Profile')}</h2>
          <div class="muted small" style="margin:3px 0 6px">${bannerMeta}</div>
          <div style="display:flex;gap:6px">${rolePill}</div>
        </div>
      </div>
      <form id="profileform">
        <div class="formgrid">
          <div class="field"><label>FULL NAME</label><input name="full_name" required value="${esc(p.full_name)}"></div>
          <div class="field"><label>DEPARTMENT</label><input name="department" value="${esc(p.department)}" placeholder="e.g. Computer Science & Engineering"></div>
          <div class="field"><label>GRADUATION YEAR</label><input name="graduation_year" type="number" min="1950" max="2100" value="${esc(p.graduation_year || '')}" placeholder="e.g. 2020"></div>
          ${!isAdmin ? `
            <div class="field"><label>${isAlumni ? 'CURRENT EMPLOYER / COMPANY' : 'COMPANY'}</label><input name="company" value="${esc(p.company)}" placeholder="${isAlumni ? 'e.g. Google, Zoho, Freshworks' : 'e.g. Internship or desired company'}"></div>
            <div class="field"><label>${isAlumni ? 'CURRENT JOB TITLE' : 'JOB TITLE'}</label><input name="job_title" value="${esc(p.job_title)}" placeholder="${isAlumni ? 'e.g. Senior Software Engineer' : 'e.g. Aspiring Backend Developer'}"></div>
            <div class="field"><label>${isAlumni ? 'MENTORSHIP EXPERTISE & SKILLS (comma-separated)' : 'SKILLS (comma-separated)'}</label><input name="skills" value="${esc((p.skills || []).join(', '))}" placeholder="e.g. System Design, Java, Python, SQL"></div>
            <div class="field"><label>${isAlumni ? 'MENTORING DOMAINS / TOPICS' : 'CAREER GOAL'}</label><input name="career_goal" value="${esc(p.career_goal)}" placeholder="${isAlumni ? 'e.g. Mock Interviews, Placement Guidance, Backend' : 'e.g. Machine Learning Engineer'}"></div>
          ` : ''}
        </div>
        ${!isAdmin ? `
          <div class="field"><label>${isAlumni ? 'INDUSTRY INTERESTS & SPECIALIZATIONS' : 'INTERESTS'}</label><textarea name="interests" placeholder="${isAlumni ? 'e.g. Distributed systems, AI deployment, career switching advice' : 'e.g. Competitive programming, web development'}">${esc(p.interests)}</textarea></div>
          <div class="field"><label>${isAlumni ? 'HOW YOU CAN HELP STUDENTS / BIO' : 'ABOUT YOU'}</label><textarea name="bio" placeholder="${isAlumni ? 'Share your journey, areas you are excited to mentor on, and how students can reach out…' : 'Tell alumni about your background, projects, and what guidance you are seeking…'}">${esc(p.bio)}</textarea></div>
        ` : `
          <div class="field"><label>ADMINISTRATIVE RESPONSIBILITIES &amp; NOTES</label><textarea name="bio">${esc(p.bio)}</textarea></div>
        `}
        <button class="btn primary">Save profile</button>
      </form>
    </div>`
  );

  $('profileform').onsubmit = async e => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.target));
    d.graduation_year = d.graduation_year ? Number(d.graduation_year) : null;
    d.skills = d.skills ? d.skills.split(',').map(x => x.trim()).filter(Boolean) : [];
    try {
      const updated = await api('/profiles/me', { method: 'PUT', body: JSON.stringify(d) });
      me.profile = updated;
      toast('Profile saved.');
      profilePage();
    } catch (err) {
      toast(err.message);
    }
  };
}

async function adminPage() {
  await adminDashboard();
}

async function verify(id, value) {
  try {
    await api(`/admin/users/${id}/verify?verified=${value}`, { method: 'PATCH' });
    toast(value ? 'Alumni profile verified.' : 'Alumni verification removed.');
    if (currentPage === 'admin_verifications') await adminVerificationsPage();
    else if (currentPage === 'admin_users') await adminUsersPage();
    else await adminDashboard();
  } catch (e) {
    toast(e.message);
  }
}

async function toggleActive(id, value) {
  try {
    await api(`/admin/users/${id}/active?active=${value}`, { method: 'PATCH' });
    toast(value ? 'Account enabled.' : 'Account disabled.');
    if (currentPage === 'admin_verifications') await adminVerificationsPage();
    else if (currentPage === 'admin_users') await adminUsersPage();
    else await adminDashboard();
  } catch (e) {
    toast(e.message);
  }
}

function logout() {
  token = '';
  me = null;
  localStorage.removeItem('ac_token');
  topbar();
  authView('login');
}

document.addEventListener('click', async event => {
  const control = event.target.closest('[data-action],[data-go],[data-auth-mode],[data-connect-id],[data-mentor-id],[data-notification-id],[data-connection-id],[data-mentorship-id],[data-user-id],[data-role-filter]');
  if (!control || control.closest('#feedPosts')) return;
  try {
    if (control.dataset.go) {
      await go(control.dataset.go);
      return;
    }
    if (control.dataset.roleFilter) {
      adminUserFilter = control.dataset.roleFilter;
      renderAdminUsersTable();
      return;
    }
    if (control.dataset.authMode) {
      authView(control.dataset.authMode);
      return;
    }
    if (control.dataset.connectId) {
      await connect(Number(control.dataset.connectId));
      return;
    }
    if (control.dataset.mentorId) {
      await mentor(Number(control.dataset.mentorId));
      return;
    }
    if (control.dataset.notificationId) {
      await readNotification(Number(control.dataset.notificationId));
      return;
    }
    if (control.dataset.connectionId) {
      await respondConnection(Number(control.dataset.connectionId), control.dataset.status);
      return;
    }
    if (control.dataset.mentorshipId) {
      await respondMentor(Number(control.dataset.mentorshipId), control.dataset.status);
      return;
    }
    if (control.dataset.userId) {
      if (control.dataset.verified !== undefined) {
        await verify(Number(control.dataset.userId), control.dataset.verified === 'true');
      } else if (control.dataset.active !== undefined) {
        await toggleActive(Number(control.dataset.userId), control.dataset.active === 'true');
      }
      return;
    }
    if (control.dataset.action === 'logout') logout();
    else if (control.dataset.action === 'retry') await renderApp();
  } catch (error) {
    toast(error.message);
  }
});

(async () => {
  topbar();
  if (token) {
    try {
      me = await api('/auth/me');
      topbar();
      currentPage = me.role === 'admin' ? 'admin' : 'dashboard';
      await renderApp();
    } catch {
      logout();
    }
  } else {
    authView('login');
  }
})();
