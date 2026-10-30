const token = location.hash.slice(1); if (token) { document.getElementById('invitation-token').value = token; history.replaceState(null, '', location.pathname); }
