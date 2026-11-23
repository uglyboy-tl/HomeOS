/* 助手页：只有模式切换，功能还没接（发送按钮先不发请求）。
   切模式不重新加载页面，墨水屏上就少一次整屏刷新。 */
(() => {
  const tabs = document.querySelector('.agent-tabs');
  const line = document.querySelector('.agent-log');
  const form = document.querySelector('.agent-say');
  const say = form && form.querySelector('textarea');
  if (!tabs || !line || !form || !say) return;

  const hint = {
    chat: '聊天模式：问什么答什么，不改页面。',
    design: '设计模式：改屏幕的样子，加一页、换个颜色。',
    device: '高级模式：动局域网里的别的设备，查服务状态。',
  };

  tabs.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-mode]');
    if (!btn || btn.getAttribute('aria-current') === 'true') return;
    for (const b of tabs.querySelectorAll('button[data-mode]')) {
      if (b === btn) b.setAttribute('aria-current', 'true');
      else b.removeAttribute('aria-current');
    }
    line.textContent = '';                       // 换模式开一段新对话
    const tip = document.createElement('p');
    tip.className = 'agent-empty';
    tip.textContent = hint[btn.dataset.mode] || '';
    line.appendChild(tip);
  });

  // 还没接后端：拦住表单默认提交（否则按“发送”会整页重载，输的内容也没了）
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    say.focus();
  });
})();
