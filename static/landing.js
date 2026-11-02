// Remember the league this browser used last, so the home page opens on it.
// Picking a league from a link (the league switch, the footer) counts; the wordmark just goes home.
(() => {
    const PAGES = { '/': 'nhl', '/pwhl': 'pwhl', '/nba': 'nba', '/mlb': 'mlb' };
    const remember = league => { try { localStorage.setItem('last_league', league); } catch (e) {} };
    if (PAGES[location.pathname]) remember(PAGES[location.pathname]);
    document.addEventListener('click', e => {
        const a = e.target.closest('a[href]');
        if (!a || a.classList.contains('wordmark')) return;
        const league = PAGES[new URL(a.href, location.href).pathname];
        if (league) remember(league);
    });
})();
