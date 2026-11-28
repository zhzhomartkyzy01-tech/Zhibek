// Сервер «Тайного Санты» (/santa/). Хранилище: Netlify Blobs, store "santa".
//
// Анонимность:
// - Пары нигде не хранятся в открытом виде. У каждого участника есть пара ключей X25519.
//   Закрытый ключ зашифрован ключом, полученным из его PIN (scrypt), и без PIN не читается.
// - При жеребьёвке сервер шифрует «кому дарить» открытым ключом дарителя и тут же забывает пары.
// - Организатор тоже может посмотреть пары: при жеребьёвке полный список шифруется его ключом,
//   а закрытый ключ организатора защищён паролем организатора. Без пароля список не прочитать.
//
// Записи:  rooms/<CODE>/meta          — название, бюджет, дата, пароль организатора, статус
//          rooms/<CODE>/p/<id>        — участник: имя, пожелание, хеш PIN, ключи, зашифрованная пара
import { getStore } from "@netlify/blobs";
import crypto from "node:crypto";

const CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";
const CODE_RE = /^[A-HJ-NP-Z2-9]{6}$/;
const PIN_RE = /^\d{4,8}$/;
const MAX_FAILS = 10, LOCK_MS = 15 * 60 * 1000;
const TOKEN_DAYS = 120;
const MAX_PEOPLE = 200;

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
// Сообщения об ошибках на казахском (клиент выбирает message или message_kk по языку)
const KK = {
  "Пароль организатора: не короче 6 символов.": "Ұйымдастырушы құпиясөзі кемінде 6 таңбадан тұруы керек.",
  "Не найдено.": "Табылмады.",
  "Игра с таким кодом не найдена. Проверьте код.": "Мұндай кодпен ойын табылмады. Кодты тексеріңіз.",
  "Жеребьёвка уже прошла, регистрация закрыта. Попросите организатора перезапустить её.": "Жеребе тартылып қойды, тіркеу жабық. Ұйымдастырушыдан жеребені қайта бастауды сұраңыз.",
  "Напишите имя (можно с фамилией), чтобы Санта понял, кому дарить.": "Санта кімге сыйлық беретінін түсінуі үшін атыңызды жазыңыз (тегімен бірге болады).",
  "PIN: от 4 до 8 цифр.": "PIN: 4-тен 8-ге дейін цифр.",
  "В игре уже слишком много участников.": "Ойында қатысушылар тым көп.",
  "Такое имя уже есть. Если это вы, нажмите «Я уже участвую». Если нет, добавьте фамилию.": "Мұндай есім бар. Бұл сіз болсаңыз, «Мен қатысып жүрмін» бөлімін басыңыз. Әйтпесе, тегіңізді қосыңыз.",
  "Неверное имя или PIN.": "Есім немесе PIN қате.",
  "Слишком много неудачных попыток. Попробуйте через 15 минут.": "Сәтсіз әрекет тым көп. 15 минуттан кейін қайталап көріңіз.",
  "Войдите снова.": "Қайта кіріңіз.",
  "Вас удалили из игры или игра перезапущена. Зарегистрируйтесь снова.": "Сіз ойыннан шығарылдыңыз немесе ойын қайта басталды. Қайта тіркеліңіз.",
  "После жеребьёвки выйти нельзя. Напишите организатору.": "Жеребеден кейін шығуға болмайды. Ұйымдастырушыға жазыңыз.",
  "Войдите как организатор.": "Ұйымдастырушы ретінде кіріңіз.",
  "Неверный пароль организатора.": "Ұйымдастырушы құпиясөзі қате.",
  "Сначала перезапустите жеребьёвку.": "Алдымен жеребені қайта бастаңыз.",
  "Жеребьёвка уже проведена.": "Жеребе тартылып қойған.",
  "Нужно хотя бы 3 участника.": "Кемінде 3 қатысушы керек.",
  "Жеребьёвка ещё не проведена.": "Жеребе әлі тартылған жоқ.",
  "Эта жеребьёвка прошла до появления функции. Перезапустите жеребьёвку, и пары станут видны.": "Бұл жеребе осы мүмкіндік қосылмай тұрып өткен. Жеребені қайта бастаңыз, сонда жұптар көрінеді.",
  "Новый PIN: от 4 до 8 цифр.": "Жаңа PIN: 4-тен 8-ге дейін цифр.",
  "Участник не найден.": "Қатысушы табылмады.",
  "Для этой жеребьёвки сменить PIN нельзя. Перезапустите жеребьёвку.": "Бұл жеребе үшін PIN ауыстыруға болмайды. Жеребені қайта бастаңыз.",
  "Игра с таким кодом не найдена.": "Мұндай кодпен ойын табылмады.",
  "Ошибка сервера. Попробуйте ещё раз.": "Сервер қатесі. Қайталап көріңіз."
};
const fail = (status, error, message) => json({ error, message, message_kk: KK[message] || message }, status);
const clean = (s, max) => String(s ?? "").replace(/[\u0000-\u0008\u000B-\u001F\u007F]/g, "").trim().slice(0, max);
const nameKey = (s) => clean(s, 60).toLowerCase().replace(/\s+/g, " ").replace(/ё/g, "е");

// --- PIN: один вызов scrypt даёт и проверочный хеш, и ключ для закрытого ключа ---
function derive(pin, salt) {
  const b = crypto.scryptSync(String(pin), Buffer.from(salt, "hex"), 64);
  return { check: b.subarray(0, 32), key: b.subarray(32) };
}
const DUMMY_SALT = "00".repeat(16);

function aesSeal(key, data) {
  const iv = crypto.randomBytes(12);
  const c = crypto.createCipheriv("aes-256-gcm", key, iv);
  const out = Buffer.concat([c.update(data), c.final()]);
  return [iv, c.getAuthTag(), out].map((x) => x.toString("base64")).join(".");
}
function aesOpen(key, s) {
  const [iv, tag, out] = s.split(".").map((x) => Buffer.from(x, "base64"));
  const d = crypto.createDecipheriv("aes-256-gcm", key, iv);
  d.setAuthTag(tag);
  return Buffer.concat([d.update(out), d.final()]);
}

// Шифрование для владельца открытого ключа X25519 (ECIES: ECDH + HKDF + AES-GCM)
function sealFor(pubPem, text) {
  const eph = crypto.generateKeyPairSync("x25519");
  const shared = crypto.diffieHellman({ privateKey: eph.privateKey, publicKey: crypto.createPublicKey(pubPem) });
  const key = Buffer.from(crypto.hkdfSync("sha256", shared, Buffer.alloc(0), "santa-pair", 32));
  return eph.publicKey.export({ type: "spki", format: "pem" }) + "|" + aesSeal(key, Buffer.from(text));
}
function openWith(privPem, sealed) {
  const [ephPem, box] = sealed.split("|");
  const shared = crypto.diffieHellman({ privateKey: crypto.createPrivateKey(privPem), publicKey: crypto.createPublicKey(ephPem) });
  const key = Buffer.from(crypto.hkdfSync("sha256", shared, Buffer.alloc(0), "santa-pair", 32));
  return aesOpen(key, box).toString();
}

function makeKeys(pin) {
  const salt = crypto.randomBytes(16).toString("hex");
  const { check, key } = derive(pin, salt);
  const kp = crypto.generateKeyPairSync("x25519");
  return {
    salt, check: check.toString("hex"),
    pub: kp.publicKey.export({ type: "spki", format: "pem" }),
    priv: aesSeal(key, Buffer.from(kp.privateKey.export({ type: "pkcs8", format: "pem" }))),
  };
}

// Ключи организатора: закрытый ключ зашифрован паролем организатора.
function ensureAdminKeys(meta, password) {
  if (meta.adminPub) return false;
  const k = makeKeys(password);
  meta.adminPub = k.pub; meta.adminPriv = k.priv; meta.adminKSalt = k.salt;
  return true;
}
function adminPairs(meta, password) {
  if (!meta.pairs || !meta.adminPriv) return null;
  const { key } = derive(password, meta.adminKSalt);
  const privPem = aesOpen(key, meta.adminPriv).toString();
  return JSON.parse(openWith(privPem, meta.pairs)); // [[giverId, receiverId], ...]
}

function newParticipant(name, pin, wish) {
  return {
    id: crypto.randomBytes(9).toString("base64url"),
    name, nk: nameKey(name), wish,
    ...makeKeys(pin),
    pair: null, fails: 0, lockUntil: 0, created: Date.now(),
  };
}

// --- токены сеанса ---
async function secretOf(st) {
  if (process.env.AUTH_SECRET) return process.env.AUTH_SECRET + ":santa";
  let s = await st.get("meta/secret");
  if (!s) { s = crypto.randomBytes(32).toString("hex"); await st.set("meta/secret", s); }
  return s;
}
async function sign(st, payload) {
  const p = Buffer.from(JSON.stringify({ ...payload, e: Date.now() + TOKEN_DAYS * 864e5 })).toString("base64url");
  return p + "." + crypto.createHmac("sha256", await secretOf(st)).update(p).digest("base64url");
}
async function readToken(st, req) {
  const t = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  const [p, sig] = t.split(".");
  if (!p || !sig) return null;
  const good = crypto.createHmac("sha256", await secretOf(st)).update(p).digest("base64url");
  if (sig.length !== good.length || !crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(good))) return null;
  try {
    const d = JSON.parse(Buffer.from(p, "base64url").toString());
    return d && d.e > Date.now() ? d : null;
  } catch { return null; }
}

const metaKey = (code) => `rooms/${code}/meta`;
const pKey = (code, id) => `rooms/${code}/p/${id}`;
async function listPeople(st, code) {
  const { blobs } = await st.list({ prefix: `rooms/${code}/p/` });
  const people = await Promise.all(blobs.map((b) => st.get(b.key, { type: "json" })));
  return people.filter(Boolean).sort((a, b) => a.created - b.created);
}
const publicRoom = (meta, people) => ({
  code: meta.code, title: meta.title, budget: meta.budget, date: meta.date, note: meta.note,
  title_kk: meta.title_kk || "", note_kk: meta.note_kk || "",
  drawn: !!meta.drawnAt, count: people.length,
  names: people.map((p) => p.name).sort((a, b) => a.localeCompare(b, "ru")),
});
function roomSettings(body) {
  return {
    title: clean(body.title, 80) || "Тайный Санта",
    budget: clean(body.budget, 40),
    date: clean(body.date, 40),
    note: clean(body.note, 300),
    title_kk: clean(body.title_kk, 80),
    note_kk: clean(body.note_kk, 300),
  };
}

// Случайный цикл: каждый дарит следующему. Никто не дарит себе, нет пар «друг другу» (при 3+).
function drawCycle(people) {
  const a = people.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = crypto.randomInt(i + 1);
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a.map((giver, i) => [giver, a[(i + 1) % a.length]]);
}

export async function handle(req, st) {
  const url = new URL(req.url);
  const parts = url.pathname.replace(/^\/santa-api\/?/, "").replace(/\/$/, "").split("/").filter(Boolean);
  const m = req.method;
  let body = {};
  if (m !== "GET") { try { body = await req.json(); } catch { body = {}; } }

  // POST /santa-api/create — новая игра
  if (parts[0] === "create" && m === "POST") {
    const password = String(body.password ?? "");
    if (password.length < 6 || password.length > 200) return fail(400, "invalid", "Пароль организатора: не короче 6 символов.");
    let code;
    for (let i = 0; i < 20; i++) {
      code = Array.from({ length: 6 }, () => CODE_ALPHABET[crypto.randomInt(CODE_ALPHABET.length)]).join("");
      if (!(await st.get(metaKey(code)))) break;
    }
    const salt = crypto.randomBytes(16).toString("hex");
    const meta = {
      code, ...roomSettings(body), salt,
      adminHash: crypto.scryptSync(password, salt, 64).toString("hex"),
      created: Date.now(), drawnAt: 0, adminV: 0,
    };
    ensureAdminKeys(meta, password);
    await st.setJSON(metaKey(code), meta);
    return json({ code, adminToken: await sign(st, { r: code, a: 1, v: 0 }) });
  }

  if (parts[0] !== "room" || !parts[1]) return fail(404, "not_found", "Не найдено.");
  const code = parts[1].toUpperCase();
  if (!CODE_RE.test(code)) return fail(404, "no_room", "Игра с таким кодом не найдена. Проверьте код.");
  const meta = await st.get(metaKey(code), { type: "json" });
  if (!meta) return fail(404, "no_room", "Игра с таким кодом не найдена. Проверьте код.");
  const action = parts.slice(2).join("/");

  // GET /room/CODE — публичные сведения
  if (action === "" && m === "GET") return json(publicRoom(meta, await listPeople(st, code)));

  // POST /room/CODE/join — регистрация участника
  if (action === "join" && m === "POST") {
    if (meta.drawnAt) return fail(409, "drawn", "Жеребьёвка уже прошла, регистрация закрыта. Попросите организатора перезапустить её.");
    const name = clean(body.name, 60).replace(/\s+/g, " ");
    const wish = clean(body.wish, 600);
    if (name.length < 2) return fail(400, "invalid", "Напишите имя (можно с фамилией), чтобы Санта понял, кому дарить.");
    if (!PIN_RE.test(String(body.pin ?? ""))) return fail(400, "invalid", "PIN: от 4 до 8 цифр.");
    const people = await listPeople(st, code);
    if (people.length >= MAX_PEOPLE) return fail(409, "full", "В игре уже слишком много участников.");
    if (people.some((p) => p.nk === nameKey(name))) return fail(409, "taken", "Такое имя уже есть. Если это вы, нажмите «Я уже участвую». Если нет, добавьте фамилию.");
    const p = newParticipant(name, body.pin, wish);
    await st.setJSON(pKey(code, p.id), p);
    return json({ token: await sign(st, { r: code, p: p.id }), id: p.id, name: p.name });
  }

  // POST /room/CODE/login — вход по имени и PIN; после жеребьёвки возвращает подопечного
  if (action === "login" && m === "POST") {
    const people = await listPeople(st, code);
    const p = people.find((x) => x.nk === nameKey(body.name));
    if (!p) { derive("0000", DUMMY_SALT); return fail(401, "bad_credentials", "Неверное имя или PIN."); }
    if (p.lockUntil > Date.now()) return fail(429, "locked", "Слишком много неудачных попыток. Попробуйте через 15 минут.");
    const { check, key } = derive(String(body.pin ?? ""), p.salt);
    if (!crypto.timingSafeEqual(check, Buffer.from(p.check, "hex"))) {
      p.fails = (p.fails || 0) + 1;
      if (p.fails >= MAX_FAILS) { p.lockUntil = Date.now() + LOCK_MS; p.fails = 0; }
      await st.setJSON(pKey(code, p.id), p);
      return fail(401, "bad_credentials", "Неверное имя или PIN.");
    }
    if (p.fails || p.lockUntil) { p.fails = 0; p.lockUntil = 0; await st.setJSON(pKey(code, p.id), p); }
    let receiverId = null;
    if (meta.drawnAt && p.pair) {
      const privPem = aesOpen(key, p.priv).toString();
      receiverId = openWith(privPem, p.pair);
    }
    return json({ token: await sign(st, { r: code, p: p.id }), id: p.id, name: p.name, receiverId });
  }

  const tok = await readToken(st, req);
  if (!tok || tok.r !== code) return fail(401, "unauthorized", "Войдите снова.");

  // --- участник ---
  if (tok.p) {
    const me = await st.get(pKey(code, tok.p), { type: "json" });
    if (!me) return fail(401, "unauthorized", "Вас удалили из игры или игра перезапущена. Зарегистрируйтесь снова.");

    // GET /room/CODE/me?receiver=ID — мои данные и пожелание подопечного
    if (action === "me" && m === "GET") {
      let receiver = null;
      const rid = url.searchParams.get("receiver");
      if (meta.drawnAt && rid && /^[\w-]{6,20}$/.test(rid) && rid !== me.id) {
        const r = await st.get(pKey(code, rid), { type: "json" });
        if (r) receiver = { name: r.name, wish: r.wish };
      }
      return json({ room: publicRoom(meta, await listPeople(st, code)), me: { id: me.id, name: me.name, wish: me.wish }, hasPair: !!me.pair, receiver });
    }
    // POST /room/CODE/wish
    if (action === "wish" && m === "POST") {
      me.wish = clean(body.wish, 600);
      await st.setJSON(pKey(code, me.id), me);
      return json({ ok: true, wish: me.wish });
    }
    // POST /room/CODE/leave — выйти из игры до жеребьёвки
    if (action === "leave" && m === "POST") {
      if (meta.drawnAt) return fail(409, "drawn", "После жеребьёвки выйти нельзя. Напишите организатору.");
      await st.delete(pKey(code, me.id));
      return json({ ok: true });
    }
    return fail(404, "not_found", "Не найдено.");
  }

  // --- организатор ---
  if (!tok.a || (meta.adminV || 0) !== tok.v) return fail(401, "unauthorized", "Войдите как организатор.");
  return adminRoutes(st, code, meta, action, m, body);
}

// Повторная проверка пароля организатора для действий с парами (с блокировкой после 10 ошибок).
async function checkAdminPassword(st, code, meta, password) {
  if (meta.lockUntil > Date.now()) return fail(429, "locked", "Слишком много неудачных попыток. Попробуйте через 15 минут.");
  const h = crypto.scryptSync(String(password ?? ""), meta.salt, 64);
  if (!crypto.timingSafeEqual(h, Buffer.from(meta.adminHash, "hex"))) {
    meta.fails = (meta.fails || 0) + 1;
    if (meta.fails >= MAX_FAILS) { meta.lockUntil = Date.now() + LOCK_MS; meta.fails = 0; }
    await st.setJSON(metaKey(code), meta);
    return fail(401, "bad_credentials", "Неверный пароль организатора.");
  }
  let changed = ensureAdminKeys(meta, String(password));
  if (meta.fails || meta.lockUntil) { meta.fails = 0; meta.lockUntil = 0; changed = true; }
  if (changed) await st.setJSON(metaKey(code), meta);
  return null;
}

async function adminRoutes(st, code, meta, action, m, body) {
  if (action === "admin" && m === "GET") {
    const people = await listPeople(st, code);
    return json({
      room: publicRoom(meta, people),
      people: people.map((p) => ({ id: p.id, name: p.name, hasWish: !!p.wish })),
      drawnAt: meta.drawnAt,
    });
  }
  if (action === "admin/settings" && m === "POST") {
    Object.assign(meta, roomSettings(body));
    await st.setJSON(metaKey(code), meta);
    return json({ ok: true });
  }
  if (action === "admin/remove" && m === "POST") {
    if (meta.drawnAt) return fail(409, "drawn", "Сначала перезапустите жеребьёвку.");
    await st.delete(pKey(code, String(body.id ?? "").replace(/[^\w-]/g, "")));
    return json({ ok: true });
  }
  if (action === "admin/draw" && m === "POST") {
    if (meta.drawnAt) return fail(409, "drawn", "Жеребьёвка уже проведена.");
    const people = await listPeople(st, code);
    if (people.length < 3) return fail(400, "few", "Нужно хотя бы 3 участника.");
    const cycle = drawCycle(people);
    for (const [giver, receiver] of cycle) {
      giver.pair = sealFor(giver.pub, receiver.id);
    }
    await Promise.all(people.map((p) => st.setJSON(pKey(code, p.id), p)));
    meta.pairs = meta.adminPub ? sealFor(meta.adminPub, JSON.stringify(cycle.map(([g, r]) => [g.id, r.id]))) : null;
    meta.drawnAt = Date.now();
    await st.setJSON(metaKey(code), meta);
    return json({ ok: true, count: people.length });
  }
  if (action === "admin/undraw" && m === "POST") {
    const people = await listPeople(st, code);
    await Promise.all(people.filter((p) => p.pair).map((p) => st.setJSON(pKey(code, p.id), { ...p, pair: null })));
    meta.drawnAt = 0; meta.pairs = null;
    await st.setJSON(metaKey(code), meta);
    return json({ ok: true });
  }
  // POST admin/pairs {password} — список «кто кому дарит», только с паролем организатора
  if (action === "admin/pairs" && m === "POST") {
    const bad = await checkAdminPassword(st, code, meta, body.password);
    if (bad) return bad;
    if (!meta.drawnAt) return fail(409, "not_drawn", "Жеребьёвка ещё не проведена.");
    const list = adminPairs(meta, String(body.password));
    if (!list) return fail(409, "no_pairs", "Эта жеребьёвка прошла до появления функции. Перезапустите жеребьёвку, и пары станут видны.");
    const byId = Object.fromEntries((await listPeople(st, code)).map((p) => [p.id, p.name]));
    return json({ pairs: list.map(([g, r]) => ({ giver: byId[g] || "(удалён)", receiver: byId[r] || "(удалён)" })) });
  }
  // POST admin/resetpin {id, pin, password} — новый PIN участнику, пара сохраняется
  if (action === "admin/resetpin" && m === "POST") {
    if (!PIN_RE.test(String(body.pin ?? ""))) return fail(400, "invalid", "Новый PIN: от 4 до 8 цифр.");
    const bad = await checkAdminPassword(st, code, meta, body.password);
    if (bad) return bad;
    const id = String(body.id ?? "").replace(/[^\w-]/g, "");
    const p = await st.get(pKey(code, id), { type: "json" });
    if (!p) return fail(404, "not_found", "Участник не найден.");
    let receiverId = null;
    if (meta.drawnAt && p.pair) {
      const list = adminPairs(meta, String(body.password));
      receiverId = list?.find(([g]) => g === id)?.[1];
      if (!receiverId) return fail(409, "no_pairs", "Для этой жеребьёвки сменить PIN нельзя. Перезапустите жеребьёвку.");
    }
    Object.assign(p, makeKeys(body.pin), { fails: 0, lockUntil: 0 });
    p.pair = receiverId ? sealFor(p.pub, receiverId) : null;
    await st.setJSON(pKey(code, id), p);
    return json({ ok: true });
  }
  return fail(404, "not_found", "Не найдено.");
}

async function adminLogin(st, req, code) {
  let body = {};
  try { body = await req.clone().json(); } catch {}
  const meta = await st.get(metaKey(code), { type: "json" });
  if (!meta) return fail(404, "no_room", "Игра с таким кодом не найдена.");
  if (meta.lockUntil > Date.now()) return fail(429, "locked", "Слишком много неудачных попыток. Попробуйте через 15 минут.");
  const h = crypto.scryptSync(String(body.password ?? ""), meta.salt, 64);
  if (!crypto.timingSafeEqual(h, Buffer.from(meta.adminHash, "hex"))) {
    meta.fails = (meta.fails || 0) + 1;
    if (meta.fails >= MAX_FAILS) { meta.lockUntil = Date.now() + LOCK_MS; meta.fails = 0; }
    await st.setJSON(metaKey(code), meta);
    return fail(401, "bad_credentials", "Неверный пароль организатора.");
  }
  const keysAdded = ensureAdminKeys(meta, String(body.password));
  if (meta.fails || meta.lockUntil || keysAdded) { meta.fails = 0; meta.lockUntil = 0; await st.setJSON(metaKey(code), meta); }
  return json({ adminToken: await sign(st, { r: code, a: 1, v: meta.adminV || 0 }) });
}

export async function route(req, st) {
  try {
    const mm = new URL(req.url).pathname.match(/^\/santa-api\/room\/([^/]+)\/admin\/login\/?$/);
    if (mm && req.method === "POST") {
      const code = mm[1].toUpperCase();
      if (!CODE_RE.test(code)) return fail(404, "no_room", "Игра с таким кодом не найдена.");
      return await adminLogin(st, req, code);
    }
    return await handle(req, st);
  } catch (e) {
    console.error(e);
    return fail(500, "server", "Ошибка сервера. Попробуйте ещё раз.");
  }
}

export default (req) => route(req, getStore({ name: "santa", consistency: "strong" }));

export const config = { path: "/santa-api/*" };
