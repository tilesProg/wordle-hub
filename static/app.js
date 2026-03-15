// static/app.js - general behaviors used by pages
document.addEventListener('DOMContentLoaded', () => {
  // Generic small helper: show inline flash messages
  const flash = (msg, cls='') => {
    const el = document.createElement('div');
    el.className = 'notice ' + cls;
    el.textContent = msg;
    const mainContainer = document.querySelector('main .container') || document.querySelector('main');
    mainContainer.prepend(el);
    setTimeout(()=> el.remove(), 4500);
  };

  // Wizard logic used on create_game page
  const wizardForm = document.querySelector('#wizard');
  if (wizardForm) {
    const MIN = 1, MAX = 6;
    let count = parseInt(document.getElementById('count')?.dataset?.init || 3, 10) || 3;
    const catsDiv = document.getElementById('cats');
    const countSpan = document.getElementById('count');
    function renderCats(){
      catsDiv.innerHTML = '';
      for (let i=0;i<count;i++){
        const wrapper = document.createElement('div');
        wrapper.className = 'form-group';
        wrapper.innerHTML = `
          <label for="cat-name-${i}">Category Name</label>
          <input id="cat-name-${i}" class="cat-name" data-idx="${i}" type="text" placeholder="e.g., Colors, Animals">
          <label for="cat-kind-${i}" style="margin-top: 1rem;">Type</label>
          <select id="cat-kind-${i}" class="cat-kind" data-idx="${i}">
            <option value="list">List of words</option>
            <option value="date">Date</option>
            <option value="number">Number</option>
          </select>
        `;
        catsDiv.appendChild(wrapper);
      }
      countSpan.textContent = count;
    }
    document.getElementById('inc')?.addEventListener('click', ()=>{ if(count<MAX){ count++; renderCats(); }});
    document.getElementById('dec')?.addEventListener('click', ()=>{ if(count>MIN){ count--; renderCats(); }});
    renderCats();

    document.getElementById('create')?.addEventListener('click', async (e)=>{
      e.preventDefault();
      const name = document.getElementById('gname').value.trim();
      const description = document.getElementById('gdesc').value.trim();
      const max_guesses = document.getElementById('max_guesses').value || null;
      const access = document.getElementById('access').value;
      const categories = Array.from(document.querySelectorAll('.cat-name')).map((el,i)=>{
        return { name: el.value.trim() || `Field ${i+1}`, kind: document.querySelectorAll('.cat-kind')[i].value };
      });
      const payload = { name, description, categories, max_guesses, access };
      try {
        const res = await fetch(location.href, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
        const data = await res.json();
        if (data.ok) { location.href = '/game/' + data.game_id + '/edit'; }
        else flash(data.error || 'Failed to create', 'danger');
      } catch(err) { flash('Network error', 'danger'); }
    });
  }

  // Play page: attach "Start" and guess behavior handled by forms on server; only minor UI helpers here
  // Add copy-to-clipboard for link tokens on edit page
  const copyBtn = document.getElementById('copyLinkToken');
  if (copyBtn) {
    copyBtn.addEventListener('click', () => {
      const tokenEl = document.getElementById('linkToken');
      navigator.clipboard.writeText(tokenEl.textContent.trim()).then(()=> {
        flash('Link token copied');
      });
    });
  }
});
