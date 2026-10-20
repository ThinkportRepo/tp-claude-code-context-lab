// Minimal Tic Tac Toe test, run by the lab's browser_script check
// (docs/prompts-and-goal.md). It assumes no markup, since every run builds its
// own game: the board is an element with nine cells (nine children, three
// rows of three, or nine alike among a few others such as a win line), and a
// cell's mark is its text, else its markup. X plays 0, 1, 2 and O plays 3, 4,
// so X wins the top row.
//
// A restart that reloads the page starts the script again; the results saved
// before the restart let it finish instead of playing again. Not covered: a
// computer opponent, a win reported without "win", "wins", "won" or "winner",
// and a start screen without the board.
window.labAssess = async (document) => {
  const SAVED = "lab-tic-tac-toe";
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const tick = () => wait(100);
  // Dialogs would block headless Chrome: their text counts as a status line,
  // and confirm() answers yes, as a player who wants to play again would.
  const alerts = [];
  window.alert = (message) => { alerts.push(String(message)); };
  window.confirm = (message) => { alerts.push(String(message)); return true; };

  const cellsOf = (el) => {
    const kids = [...el.children];
    if (kids.length === 9) return kids;
    if (kids.length === 3 && kids.every((k) => k.children.length === 3)) {
      const grid = kids.flatMap((k) => [...k.children]);
      if (grid.every((c) => c.tagName === grid[0].tagName)) return grid;
    }
    const kind = (k) => `${k.tagName}.${k.className}`;
    const alike = kids.filter((k) => kids.filter((o) => kind(o) === kind(k)).length === 9);
    return alike.length === 9 && kids.length <= 12 ? alike : null;
  };
  const findBoard = () => {
    for (const el of document.body.querySelectorAll("*")) {
      const cells = cellsOf(el);
      if (cells) return [el, cells];
    }
    return [null, []];
  };
  // Without text, only the parts that can carry a mark count (not styles or
  // tabindex); digits are dropped, so "cell-4" or data-index="4" do not differ.
  const mark = (i, from) => from[i].textContent.trim() || [
    from[i].getAttribute("class")?.trim(), JSON.stringify({ ...from[i].dataset }),
    from[i].getAttribute("src"), from[i].innerHTML,
  ].join("|").replace(/\d+/g, "");
  const isEmpty = (from, empty) => from.length === 9 && from.every((_, i) => mark(i, from) === empty[i]);

  const saved = JSON.parse(sessionStorage.getItem(SAVED));
  if (saved) return { ...saved.steps, restarts: isEmpty(findBoard()[1], saved.empty) };

  const [board, cells] = findBoard();
  if (!board) return { board: false };
  const click = async (i) => {
    (cells[i].querySelector("button") || cells[i]).click();
    await tick();
  };
  // Lines of text outside the board, plus alerts, where a game reports the result.
  const status = () => document.body.innerText.replace(board.innerText, "").split("\n")
    .map((line) => line.trim()).concat(alerts);
  const at = (i) => mark(i, cells);
  const empty = cells.map((_, i) => at(i));

  await click(0);
  const marks = at(0) !== empty[0];
  await click(3);
  const alternates = at(3) !== empty[3] && at(3) !== at(0);
  await click(1);
  await click(4);
  const before = status();
  await click(2);
  // A new line that names X (or the mark X's cells show) and a win.
  const x = (cells[0].textContent.trim() || "X").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const won = () => status().some((line) => !before.includes(line)
    && new RegExp(`(^|[^\\w])(${x}|X)([^\\w]|$)`, "i").test(line) && /\b(wins?|won|winner)\b/i.test(line));
  let win = won();
  for (let t = 0; !win && t < 20; t++) { // the result may come after an animation
    await tick();
    win = won();
  }
  const restarted = isEmpty(cells, empty); // some games start over by themselves
  await click(8);
  const stops_after_win = restarted || at(8) === empty[8];
  const steps = { board: true, marks, alternates, win, stops_after_win };

  const label = (el) => [el.textContent, el.value, el.getAttribute("aria-label"), el.title].join(" ");
  const controls = [...document.querySelectorAll(
    'button, a, [role="button"], input[type="button"], input[type="reset"], input[type="submit"]')]
    .filter((el) => /restart|reset|new game|play again/i.test(label(el)));
  // "Reset scores" is not a restart, unless it is the only candidate.
  const restart = controls.find((el) => !/score/i.test(label(el))) || controls[0];
  if (!restart) return { ...steps, restarts: false };
  sessionStorage.setItem(SAVED, JSON.stringify({ steps, empty }));
  restart.click();
  await wait(1000);
  return { ...steps, restarts: isEmpty(findBoard()[1], empty) };
};
