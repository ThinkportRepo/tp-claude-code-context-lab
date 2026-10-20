// Minimal Tic Tac Toe test, run by the lab's browser_script check
// (docs/prompts-and-goal.md). It assumes no markup, since every run builds its
// own game: the board is an element with nine cells (nine children, nine alike
// among a few others such as a win line, or else three rows of three), and a
// cell's mark is its text, else its class, data attributes, src or the same of
// its descendants. X plays 0, 1, 2 and O plays 3, 4, so X wins the top row.
//
// A game may reload the page at the win or on restart, which starts the script
// again; what it saved before such a click lets it finish instead of playing
// again. Not covered: a computer opponent, a win reported without "win",
// "wins", "won" or "winner", and a start screen or dialog before the board.
window.labAssess = async (document) => {
  const SAVED = "lab-tic-tac-toe";
  const saved = JSON.parse(sessionStorage.getItem(SAVED));
  let pending = null; // what a reload during the next click must keep
  const save = () => sessionStorage.setItem(SAVED, JSON.stringify({ ...pending, alerts }));
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const tick = () => wait(100);
  // Dialogs would block headless Chrome: their text counts as a status line,
  // and confirm() answers yes, as a player who wants to play again would.
  const alerts = saved?.alerts || [];
  const dialog = (message) => {
    alerts.push(String(message));
    if (pending) save();
  };
  window.alert = (message) => { dialog(message); };
  window.confirm = (message) => { dialog(message); return true; };

  const flat = (el) => {
    const kids = [...el.children];
    if (kids.length === 9) return kids;
    const kind = (k) => `${k.tagName}.${k.className}`;
    const alike = kids.filter((k) => kids.filter((o) => kind(o) === kind(k)).length === 9);
    return alike.length === 9 && kids.length <= 12 ? alike : null;
  };
  const rows = (el) => {
    const kids = [...el.children];
    if (kids.length !== 3 || !kids.every((k) => k.children.length === 3)) return null;
    const grid = kids.flatMap((k) => [...k.children]);
    return grid.every((c) => c.tagName === grid[0].tagName) ? grid : null;
  };
  // A flat board anywhere wins over rows of three (which score cards can look like).
  const findBoard = () => {
    for (const cellsOf of [flat, rows]) {
      for (const el of document.body.querySelectorAll("*")) {
        const cells = cellsOf(el);
        if (cells) return [el, cells];
      }
    }
    return [null, []];
  };
  // Without text, only what can carry a mark counts (not styles, tabindex or
  // disabled); digits are dropped, so "cell-4" or data-index="4" do not differ.
  const markOf = (el) => [el.tagName, el.getAttribute("class")?.trim(), JSON.stringify({ ...el.dataset }),
    el.getAttribute("src"), el.getAttribute("alt")].join(",");
  const mark = (cell) => cell.textContent.trim()
    || [cell, ...cell.querySelectorAll("*")].map(markOf).join("|").replace(/\d+/g, "");
  const isEmpty = (cells, empty) => cells.length === 9 && cells.every((c, i) => mark(c) === empty[i]);
  const current = () => findBoard()[1];
  const click = async (cell) => {
    (cell.querySelector("button") || cell).click();
    await tick();
  };
  // A new line that names X (or the mark X's cells show) and a win.
  const isWin = (line, x, before) => !before.includes(line)
    && new RegExp(`(^|[^\\w])(${x}|X)([^\\w]|$)`, "i").test(line) && /\b(wins?|won|winner)\b/i.test(line);

  if (saved?.phase === "win") { // the page reloaded at the win: the game started over
    const win = alerts.some((line) => isWin(line, saved.x, saved.before));
    return { ...saved.steps, win, stops_after_win: true, restarts: isEmpty(current(), saved.empty) };
  }
  if (saved?.phase === "restart") return { ...saved.steps, restarts: isEmpty(current(), saved.empty) };

  const [board, cells] = findBoard();
  if (!board) return { board: false };
  // Lines of text outside the board, plus dialogs, where a game reports the result.
  const status = () => document.body.innerText.replace(board.innerText, "").split("\n")
    .map((line) => line.trim()).concat(alerts);
  const empty = cells.map(mark);

  await click(cells[0]);
  const marks = mark(cells[0]) !== empty[0];
  await click(cells[3]);
  const alternates = mark(cells[3]) !== empty[3] && mark(cells[3]) !== mark(cells[0]);
  await click(cells[1]);
  await click(cells[4]);
  const x = (cells[0].textContent.trim() || "X").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const before = status();
  pending = { phase: "win", steps: { board: true, marks, alternates }, empty, x, before };
  save();
  await click(cells[2]);
  let win = status().some((line) => isWin(line, x, before));
  for (let t = 0; !win && t < 20; t++) { // the result may come after an animation
    await tick();
    win = status().some((line) => isWin(line, x, before));
  }
  // Some games start over by themselves after a win.
  let restarted = isEmpty(current(), empty);
  for (let t = 0; !restarted && t < 20; t++) {
    await tick();
    restarted = isEmpty(current(), empty);
  }
  let stops_after_win = restarted;
  if (!restarted) {
    await click(cells[8]);
    stops_after_win = mark(cells[8]) === empty[8];
  } else {
    await click(current()[0]); // so a restart has a mark to clear
  }
  const steps = { board: true, marks, alternates, win, stops_after_win };

  const label = (el) => [el.textContent, el.value, el.getAttribute("aria-label"), el.title].join(" ");
  const controls = [...document.querySelectorAll(
    'button, a, [role="button"], input[type="button"], input[type="reset"], input[type="submit"]')]
    .filter((el) => /restart|reset|new game|play again/i.test(label(el)));
  // "Reset scores" is not a restart, unless it is the only candidate.
  const restart = controls.find((el) => !/score/i.test(label(el))) || controls[0];
  if (!restart) return { ...steps, restarts: false };
  pending = { phase: "restart", steps, empty };
  save();
  restart.click();
  await wait(1000);
  return { ...steps, restarts: isEmpty(current(), empty) };
};
