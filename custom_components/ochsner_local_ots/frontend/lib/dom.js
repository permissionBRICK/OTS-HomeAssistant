// Minimal DOM helpers: HTML escaping, event firing and an in-place morph that
// patches an element's children from an HTML string while keeping existing
// nodes (focus, pointer capture, hover and CSS transitions survive re-renders).

export const esc = (v) =>
  String(v === undefined || v === null ? "" : v)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

export function fire(node, type, detail, opts = {}) {
  const ev = new Event(type, { bubbles: opts.bubbles !== false, composed: opts.composed !== false, cancelable: !!opts.cancelable });
  ev.detail = detail;
  node.dispatchEvent(ev);
  return ev;
}

export function moreInfo(node, entityId) {
  if (entityId) fire(node, "hass-more-info", { entityId });
}

export function navigate(path) {
  history.pushState(null, "", path);
  fire(window, "location-changed", { replace: false });
}

const keyOf = (n) => (n.nodeType === 1 ? n.getAttribute("data-key") : null);

function sameNode(a, b) {
  if (a.nodeType !== b.nodeType) return false;
  if (a.nodeType !== 1) return true;
  return a.nodeName === b.nodeName && a.namespaceURI === b.namespaceURI && keyOf(a) === keyOf(b);
}

function syncAttrs(from, to) {
  const fa = from.attributes, ta = to.attributes;
  for (let i = fa.length - 1; i >= 0; i--) {
    const name = fa[i].name;
    if (!to.hasAttribute(name)) from.removeAttribute(name);
  }
  for (let i = 0; i < ta.length; i++) {
    const { name, value } = ta[i];
    if (from.getAttribute(name) !== value) from.setAttribute(name, value);
  }
}

function morphNode(from, to) {
  if (from.nodeType !== 1) {
    if (from.nodeValue !== to.nodeValue) from.nodeValue = to.nodeValue;
    return;
  }
  syncAttrs(from, to);
  if (from.hasAttribute("data-keep")) return; // children managed imperatively
  morphChildren(from, to);
}

function morphChildren(from, to) {
  let fc = from.firstChild;
  let tc = to.firstChild;
  while (tc) {
    const nextT = tc.nextSibling;
    if (!fc) {
      from.appendChild(tc);
    } else if (sameNode(fc, tc)) {
      morphNode(fc, tc);
      fc = fc.nextSibling;
    } else {
      const key = keyOf(tc);
      let match = null;
      if (key !== null) {
        for (let n = fc.nextSibling; n; n = n.nextSibling) {
          if (keyOf(n) === key && sameNode(n, tc)) { match = n; break; }
        }
      }
      if (match) {
        from.insertBefore(match, fc);
        morphNode(match, tc);
      } else if (key === null && keyOf(fc) === null) {
        const next = fc.nextSibling;
        from.replaceChild(tc, fc);
        fc = next;
      } else {
        from.insertBefore(tc, fc);
      }
    }
    tc = nextT;
  }
  while (fc) {
    const n = fc.nextSibling;
    from.removeChild(fc);
    fc = n;
  }
}

/** Patch `target`'s children to match `html`. */
export function morph(target, html) {
  const doc = target.ownerDocument || document;
  const tpl = doc.createElement("template");
  tpl.innerHTML = html;
  morphChildren(target, tpl.content);
}

/** requestAnimationFrame-batched callback. */
export function rafBatch(fn) {
  let queued = false;
  return () => {
    if (queued) return;
    queued = true;
    const raf = globalThis.requestAnimationFrame || ((cb) => setTimeout(cb, 16));
    raf(() => {
      queued = false;
      fn();
    });
  };
}
