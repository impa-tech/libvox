/*
 * LibVox — the web channel's browser client.
 *
 * One file, no build step, no dependency. A library installs it by pasting one
 * line into an OPAC template, which is the only realistic way into a Koha
 * template: there is no npm there, and no deploy pipeline to add one to.
 *
 *     <script src="https://libvox.exemplo/widget.js" data-key="wk_..."
 *             data-name="MARC.IA" data-color="#4f46e5"></script>
 *
 * `data-name` and `data-color` are there for the launcher button, which is on
 * screen before any socket opens -- and the socket only opens when somebody
 * clicks it, so the button cannot wait for the session and cannot be hidden
 * until it. Everything else about the library's voice arrives over the wire,
 * and both of these are overridden by it once it lands.
 *
 * TWO HALVES THAT DO NOT KNOW ABOUT EACH OTHER
 *
 * `render` draws from a plain configuration object. `connect` speaks the
 * socket. Nothing in the drawing knows where the configuration came from,
 * which is what lets the librarian's panel render a live preview from an
 * unsaved form -- and which is a better shape anyway.
 *
 * WHAT NEVER APPEARS HERE
 *
 * No session token: the browser sends a public widget key and the server keeps
 * the credential. No tenant id. No tool arguments and no tool results -- a
 * ToolExecuting event carries a name, and this file turns that name into a
 * sentence. If a future event carries a catalogue record for display, it
 * arrives in ResponseGenerated as prose, because deciding what a reader is
 * told is the agent's job and not this file's.
 */
(function () {
  'use strict';

  // Her name, when nothing has said otherwise. The panel header falls back to
  // the same string, so a widget with no `data-name` is consistent with itself.
  var DEFAULT_NAME = 'MARC.IA';

  // And the accent, which is also the fallback written into every `var(--accent,
  // ...)` in the stylesheet below. Named here so the two cannot drift: a
  // default in one place and a fallback in the other is how a button ends up a
  // different colour from the panel it opens.
  var DEFAULT_ACCENT = '#4f46e5';

  var STATUS = {
    'pt-BR': {
      search_catalog: 'consultando o catálogo…',
      check_availability: 'vendo se está na estante…',
      thinking: 'pensando…',
      offline: 'Sem conexão com a biblioteca.',
      reconnect: 'Reconectar',
      placeholder: 'Pergunte sobre o acervo…',
      send: 'Enviar',
      open: 'Converse com a {name}',
      close: 'Fechar',
      helpful: 'Isso ajudou?',
      yes: 'Sim, ajudou',
      no: 'Não ajudou',
      thanks: 'Obrigada por dizer.',
    },
    en: {
      search_catalog: 'searching the catalogue…',
      check_availability: 'checking the shelf…',
      thinking: 'thinking…',
      offline: 'Not connected to the library.',
      reconnect: 'Reconnect',
      placeholder: 'Ask about the collection…',
      send: 'Send',
      open: 'Chat with {name}',
      close: 'Close',
      helpful: 'Did that help?',
      yes: 'Yes, it helped',
      no: 'It did not help',
      thanks: 'Thank you for saying.',
    },
  };

  function words(locale) {
    return STATUS[locale] || STATUS['pt-BR'];
  }

  var CSS = [
    ':host{all:initial}',
    '*{box-sizing:border-box;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}',
    '.panel{display:flex;flex-direction:column;width:min(380px,calc(100vw - 32px));',
    'height:min(560px,calc(100vh - 120px));background:#fff;color:#1a1a1a;',
    'border:1px solid #e3e3e3;border-radius:14px;overflow:hidden;',
    'box-shadow:0 12px 40px rgba(0,0,0,.16)}',
    '.head{display:flex;align-items:center;gap:8px;padding:12px 14px;',
    'background:var(--accent,#4f46e5);color:#fff;font-weight:600;font-size:15px}',
    '.head button{margin-left:auto;background:transparent;border:0;color:inherit;',
    'font-size:20px;line-height:1;cursor:pointer;padding:2px 6px;border-radius:6px}',
    '.head button:focus-visible{outline:2px solid #fff;outline-offset:1px}',
    '.log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px}',
    '.msg{max-width:85%;padding:9px 12px;border-radius:12px;font-size:14px;',
    'line-height:1.5;white-space:pre-wrap;overflow-wrap:anywhere}',
    '.msg strong{font-weight:600}',
    '.msg em{font-style:italic}',
    '.msg.them{background:#f2f2f5;border-bottom-left-radius:4px;align-self:flex-start}',
    '.msg.you{background:var(--accent,#4f46e5);color:#fff;align-self:flex-end;',
    'border-bottom-right-radius:4px}',
    '.msg.problem{background:#fdf0ef;color:#8a2216;align-self:stretch;max-width:100%}',
    '.status{font-size:13px;color:#6b6b74;font-style:italic;padding:0 2px}',
    '.rate{display:flex;align-items:center;gap:6px;align-self:flex-start;',
    'margin:-4px 0 2px 2px;font-size:12px;color:#6b6b74}',
    '.rate button{display:flex;align-items:center;justify-content:center;',
    'width:24px;height:24px;padding:0;border:1px solid #dcdce2;border-radius:6px;',
    'background:transparent;color:#6b6b74;cursor:pointer}',
    '.rate button:hover:not(:disabled){border-color:var(--accent,#4f46e5);',
    'color:var(--accent,#4f46e5)}',
    '.rate button:focus-visible{outline:2px solid var(--accent,#4f46e5);outline-offset:1px}',
    '.rate button:disabled{cursor:default;opacity:.45}',
    // The chosen one keeps full opacity while its pair fades, so the answer is
    // legible at a glance after the fact.
    '.rate button[aria-pressed="true"]{opacity:1;border-color:var(--accent,#4f46e5);',
    'background:var(--accent,#4f46e5);color:#fff}',
    '.rate svg{width:14px;height:14px;fill:currentColor}',
    '.rate .down svg{transform:rotate(180deg)}',
    '.status span{display:inline-block;animation:pulse 1.4s ease-in-out infinite}',
    '@keyframes pulse{0%,100%{opacity:.45}50%{opacity:1}}',
    '@media (prefers-reduced-motion: reduce){.status span{animation:none}}',
    '.compose{display:flex;gap:8px;padding:10px;border-top:1px solid #ececf0}',
    '.compose input{flex:1;padding:9px 11px;border:1px solid #d8d8de;border-radius:9px;',
    'font-size:14px;min-width:0}',
    '.compose input:focus-visible{outline:2px solid var(--accent,#4f46e5);outline-offset:0}',
    '.compose button{background:var(--accent,#4f46e5);color:#fff;border:0;',
    'border-radius:9px;padding:0 15px;font-size:14px;font-weight:600;cursor:pointer}',
    '.compose button:disabled{opacity:.5;cursor:default}',
    '.compose button:focus-visible{outline:2px solid #1a1a1a;outline-offset:2px}',
    '.launcher{background:var(--accent,#4f46e5);color:#fff;border:0;border-radius:999px;',
    'padding:12px 20px;font-size:15px;font-weight:600;cursor:pointer;',
    'box-shadow:0 6px 20px rgba(0,0,0,.18)}',
    '.launcher:focus-visible{outline:2px solid #1a1a1a;outline-offset:2px}',
    '.floating{position:fixed;right:20px;bottom:20px;z-index:2147483000;',
    'display:flex;flex-direction:column;align-items:flex-end;gap:10px}',
  ].join('');

  /* ------------------------------------------------------- a little markdown */

  /**
   * `**bold**` and `*italic*`. Not the rest of Markdown.
   *
   * Built as DOM nodes rather than an HTML string, and that is the point of
   * this function rather than a detail of it. The text is written by a language
   * model out of tool results and the reader's own words, so `innerHTML` here
   * would be an injection path from a patron's question into the page, through
   * the one component every library embeds. The only tags this can produce are
   * `<strong>` and `<em>`, and their contents are set with `textContent`.
   *
   * **Why the italic rule looks like that.** The model writes bullets as
   * `*   Eevee`, and a naive `\*(.+?)\*` would pair that leading asterisk with
   * the next one and italicise the whitespace between them. Markdown's own rule
   * settles it: an asterisk opens emphasis only when it is not followed by
   * space, and closes only when it is not preceded by one. So the run must
   * begin and end with a non-space -- which is what `[^\s*](?:[^*]*[^\s*])?`
   * says, the optional tail being there so a single character still matches.
   *
   * Written without lookbehind on purpose. `(?<!\s)` would express the closing
   * half more directly and is unsupported in Safari before 16.4, where an
   * unsupported regex literal is a *parse* error -- it would take the whole
   * widget down, not just the italics.
   *
   * No `_italic_`: with the same non-space rule, `snake_case_name` would match
   * and emphasise the middle of an identifier.
   */
  var EMPHASIS =
    /\*\*\*([^*]+)\*\*\*|\*\*([^*]+)\*\*|\*([^\s*](?:[^*]*[^\s*])?)\*/g;

  // Leading spaces on a line, dropped before anything else looks at the text.
  //
  // The panel renders with `white-space: pre-wrap`, which is what keeps the
  // blank line between paragraphs and the break between list items -- and it
  // also renders indentation, which the model supplies whether or not anybody
  // wanted it. Its examples number a list, the source wraps those lines to
  // column five, and it copies the five spaces into the answer along with the
  // shape. The examples no longer indent; this is here because the next model,
  // or the next example, will do it again and a reader should never see it.
  //
  // Line breaks survive. Only the run of spaces at the start of one goes.
  function dedent(text) {
    return text.replace(/^[ \t]+/gm, '');
  }

  function withEmphasis(text) {
    var fragment = document.createDocumentFragment();
    var last = 0;
    var match;

    EMPHASIS.lastIndex = 0;

    while ((match = EMPHASIS.exec(text)) !== null) {
      if (match.index > last) {
        fragment.appendChild(document.createTextNode(text.slice(last, match.index)));
      }

      // Longest delimiter first in the alternation, so `***` is not read as
      // `**` with a stray asterisk left over on each side.
      var node;
      if (match[1] !== undefined) {
        node = document.createElement('strong');
        var inner = document.createElement('em');
        inner.textContent = match[1];
        node.appendChild(inner);
      } else {
        node = document.createElement(match[2] !== undefined ? 'strong' : 'em');
        node.textContent = match[2] !== undefined ? match[2] : match[3];
      }
      fragment.appendChild(node);

      last = EMPHASIS.lastIndex;
    }

    if (last < text.length) {
      fragment.appendChild(document.createTextNode(text.slice(last)));
    }

    return fragment;
  }

  /* ------------------------------------------------------------ the thumb */

  // Material's thumb_up, and thumbs-down is the same path rotated by CSS. One
  // shape rather than two, and no emoji: 👍 renders differently on every
  // platform and on a few of them it renders as a box.
  var THUMB =
    'M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79' +
    '-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0' +
    ' 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z';

  function thumb() {
    var NS = 'http://www.w3.org/2000/svg';
    // createElementNS rather than innerHTML, for the reason `withEmphasis`
    // gives at length: nothing in this file builds markup from a string.
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    var path = document.createElementNS(NS, 'path');
    path.setAttribute('d', THUMB);
    svg.appendChild(path);
    return svg;
  }

  /* ---------------------------------------------------------------- render */

  /**
   * Draw the widget into `host`, and return a handle to it.
   *
   * Knows nothing about sockets. `onSend` is optional: without it the composer
   * is disabled, which is exactly the preview the librarian's panel wants.
   */
  function render(host, config, onSend) {
    config = config || {};
    var t = words(config.locale);  // reassigned by `configure`

    var root = host.shadowRoot || host.attachShadow({ mode: 'open' });
    root.innerHTML = '';

    var style = document.createElement('style');
    style.textContent = CSS;
    root.appendChild(style);

    var panel = document.createElement('div');
    panel.className = 'panel';
    panel.style.setProperty('--accent', config.theme_color || DEFAULT_ACCENT);
    panel.setAttribute('role', 'log');
    panel.setAttribute('aria-live', 'polite');

    var head = document.createElement('div');
    head.className = 'head';
    head.appendChild(document.createTextNode(config.assistant_name || DEFAULT_NAME));
    panel.appendChild(head);

    var log = document.createElement('div');
    log.className = 'log';
    panel.appendChild(log);

    var status = document.createElement('div');
    status.className = 'status';
    log.appendChild(status);

    var form = document.createElement('form');
    form.className = 'compose';
    var input = document.createElement('input');
    input.type = 'text';
    input.placeholder = t.placeholder;
    input.setAttribute('aria-label', t.placeholder);
    input.maxLength = 2000;
    var send = document.createElement('button');
    send.type = 'submit';
    send.textContent = t.send;
    form.appendChild(input);
    form.appendChild(send);
    panel.appendChild(form);

    if (!onSend) {
      input.disabled = true;
      send.disabled = true;
    }

    root.appendChild(panel);

    var api = {
      say: function (text, who) {
        var line = document.createElement('div');
        line.className = 'msg ' + (who || 'them');

        // Only what MARC.IA wrote is formatted. The reader's own message is
        // echoed exactly as typed -- there is no reason to parse it, and every
        // reason not to.
        if ((who || 'them') === 'them') {
          line.appendChild(withEmphasis(dedent(text)));
        } else {
          line.textContent = text;
        }

        log.insertBefore(line, status);
        log.scrollTop = log.scrollHeight;
        return line;
      },
      status: function (text) {
        status.textContent = '';
        if (text) {
          var dots = document.createElement('span');
          dots.textContent = text;
          status.appendChild(dots);
        }
        log.scrollTop = log.scrollHeight;
      },
      offer: function (label, action) {
        var line = document.createElement('div');
        line.className = 'status';
        var button = document.createElement('button');
        button.className = 'launcher';
        button.textContent = label;
        button.addEventListener('click', action);
        line.appendChild(button);
        log.insertBefore(line, status);
        log.scrollTop = log.scrollHeight;
      },
      /**
       * Two thumbs under one answer.
       *
       * Returns a handle with `chose(useful)`, which the socket calls when the
       * server confirms. The buttons dim on click and only fill in on that
       * confirmation: a thumb that lights up before the write lands is a thumb
       * that lies when the write does not.
       *
       * Null without `onSend`. The librarian's panel renders this same widget
       * as a preview with no socket behind it, and a rating there would have
       * nowhere to go -- the same reason the composer is disabled.
       */
      rating: function (onRate) {
        if (!onSend) return null;

        var row = document.createElement('div');
        row.className = 'rate';

        var asked = document.createElement('span');
        asked.textContent = t.helpful;
        row.appendChild(asked);

        var buttons = [true, false].map(function (useful) {
          var button = document.createElement('button');
          button.type = 'button';
          button.className = useful ? 'up' : 'down';
          button.setAttribute('aria-label', useful ? t.yes : t.no);
          button.setAttribute('aria-pressed', 'false');
          button.appendChild(thumb());
          button.addEventListener('click', function () {
            buttons.forEach(function (b) { b.disabled = true; });
            onRate(useful);
          });
          row.appendChild(button);
          return button;
        });

        log.insertBefore(row, status);
        log.scrollTop = log.scrollHeight;

        return {
          chose: function (useful) {
            buttons.forEach(function (b, i) {
              b.disabled = true;
              b.setAttribute('aria-pressed', (i === 0) === useful ? 'true' : 'false');
            });
            asked.textContent = t.thanks;
          },
        };
      },
      configure: function (next) {
        // Updated in place rather than re-rendered. Re-rendering would clear
        // the shadow root, and every handler already closed over the old log
        // element -- so answers would arrive into a node detached from the
        // page, which looks exactly like the server never replied.
        if (next.theme_color) panel.style.setProperty('--accent', next.theme_color);
        if (next.assistant_name) head.firstChild.textContent = next.assistant_name;
        if (next.locale && STATUS[next.locale]) {
          t = STATUS[next.locale];
          api.words = t;
          input.placeholder = t.placeholder;
          input.setAttribute('aria-label', t.placeholder);
          send.textContent = t.send;
        }
        if (next.welcome_message) api.say(next.welcome_message);
      },
      enable: function (on) {
        if (!onSend) return;
        input.disabled = !on;
        send.disabled = !on;
        if (on) input.focus();
      },
      focus: function () {
        input.focus();
      },
      words: t,
    };

    form.addEventListener('submit', function (event) {
      event.preventDefault();
      var text = input.value.trim();
      if (!text || !onSend) return;
      input.value = '';
      api.say(text, 'you');
      onSend(text);
    });

    if (config.welcome_message) api.say(config.welcome_message);

    return api;
  }

  /* --------------------------------------------------------------- connect */

  function connect(url, key, ui, onReady) {
    var socket;
    var answers = 0;
    var pending = {};
    try {
      socket = new WebSocket(url + '?key=' + encodeURIComponent(key));
    } catch (error) {
      ui.status(ui.words.offline);
      return null;
    }

    socket.addEventListener('message', function (event) {
      var frame;
      try {
        frame = JSON.parse(event.data);
      } catch (error) {
        return;
      }

      if (frame.type === 'SessionReadyEvent') {
        onReady(frame);
      } else if (frame.type === 'ToolExecuting') {
        ui.status(ui.words[frame.tool] || ui.words.thinking);
      } else if (frame.type === 'ResponseGenerated') {
        ui.status('');
        ui.say(frame.text);
        ui.enable(true);

        // The same count the server keeps, and the only thing a rating names.
        // Incremented here and nowhere else: the greeting is not an answer,
        // and neither is an ErrorEvent -- rating either would put this out of
        // step with the log, silently, in the direction that attaches every
        // later thumb to the answer before it.
        answers += 1;
        var ordinal = answers;
        var handle = ui.rating(function (useful) {
          socket.send(JSON.stringify({
            type: 'AnswerRated', answer: ordinal, useful: useful,
          }));
        });
        if (handle) pending[ordinal] = handle;
      } else if (frame.type === 'RatingRecorded') {
        // Only now. The server says nothing when a rating does not land, so a
        // thumb that stays dim is a thumb that was not recorded -- which is
        // less wrong than one that claims it was.
        if (pending[frame.answer]) pending[frame.answer].chose(frame.useful);
      } else if (frame.type === 'ErrorEvent') {
        ui.status('');
        ui.say(frame.message, 'problem');
        ui.enable(true);
      }
    });

    socket.addEventListener('close', function () {
      // Never silently. A widget that goes quiet reads as broken; one that says
      // it lost the library, and offers the way back, does not.
      ui.status('');
      ui.enable(false);
      ui.offer(ui.words.reconnect, function () {
        window.location.reload();
      });
    });

    return socket;
  }

  /* ------------------------------------------------------------------ boot */

  function socketUrl(src) {
    var url = new URL('/ws/v1/chat', src);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return url.toString();
  }

  function mount(options) {
    var host = document.createElement('div');
    document.body.appendChild(host);

    var wrap = host.attachShadow({ mode: 'open' });
    var style = document.createElement('style');
    style.textContent = CSS;
    var floating = document.createElement('div');
    floating.className = 'floating';
    wrap.appendChild(style);
    wrap.appendChild(floating);

    // Her name, not the building's. `data-name` carries it because the
    // launcher is on screen before any socket opens -- the configured name
    // arrives with the session, and by then the reader has already read the
    // button. The attribute is what the portal pastes into the snippet; the
    // socket's answer still wins once it lands, below.
    var name = options.name || DEFAULT_NAME;
    var launch = function () {
      return words(options.locale).open.replace('{name}', name);
    };

    // **On `floating`, not on the panel.** The launcher is the panel's sibling,
    // so an accent set on the panel never reached it -- the button stayed the
    // stylesheet's fallback purple for the whole life of the page, before the
    // session and after it, while the panel it opened was the library's colour.
    // Set here, both inherit.
    //
    // `data-color` for the same reason `data-name` exists: the button is on
    // screen before any socket is. It cannot be hidden until the colour
    // arrives, because the socket only opens when somebody clicks the button --
    // a launcher that waits for the session is a launcher nobody can click.
    var accent = options.color || DEFAULT_ACCENT;
    floating.style.setProperty('--accent', accent);

    var launcher = document.createElement('button');
    launcher.className = 'launcher';
    launcher.textContent = launch();
    floating.appendChild(launcher);

    var panelHost = document.createElement('div');
    var opened = false;
    var socket = null;
    var ui = null;

    launcher.addEventListener('click', function () {
      if (opened) {
        floating.removeChild(panelHost);
        floating.appendChild(launcher);
        launcher.textContent = launch();
        opened = false;
        if (socket) socket.close();
        socket = null;
        return;
      }

      floating.insertBefore(panelHost, launcher);
      launcher.textContent = words(options.locale).close;
      opened = true;

      ui = render(panelHost, { locale: options.locale, theme_color: accent }, function (text) {
        ui.enable(false);
        ui.status(ui.words.thinking);
        socket.send(JSON.stringify({ type: 'QueryReceived', text: text }));
      });
      ui.enable(false);

      socket = connect(options.url, options.key, ui, function (config) {
        // The library's own voice, which only arrives once the server has
        // decided who this key belongs to. Applied to the panel already on
        // screen; see `configure`.
        ui.configure(config);
        ui.enable(true);
        // A library that renamed her after the snippet was pasted. The button
        // says the old name until the first session; from here on it does not.
        if (config.assistant_name) name = config.assistant_name;
        if (config.theme_color) floating.style.setProperty('--accent', config.theme_color);
      });
    });

    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && opened) launcher.click();
    });
  }

  window.LibVox = { render: render, mount: mount };

  var tag = document.currentScript;
  if (tag && tag.dataset.key) {
    var boot = function () {
      mount({
        key: tag.dataset.key,
        url: tag.dataset.url || socketUrl(tag.src),
        locale: tag.dataset.locale || 'pt-BR',
        name: tag.dataset.name || DEFAULT_NAME,
        color: tag.dataset.color || DEFAULT_ACCENT,
      });
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', boot);
    } else {
      boot();
    }
  }
})();
