// Сервер бюджета: вход по логину и паролю, хранение данных в Netlify Blobs.
// Маршруты: /api/status, /api/register, /api/login, /api/me, /api/data, /api/password, /api/admin/*
import { getStore } from "@netlify/blobs";
import crypto from "node:crypto";

const LOGIN_RE = /^[a-z0-9._-]{3,32}$/;
const TOKEN_DAYS = 180;
const MAX_FAILS = 10, LOCK_MS = 15 * 60 * 1000;
const MAX_DATA = 4_000_000;

const json = (data, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
const fail = (status, error, message) => json({ error, message }, status);
const norm = (l) => String(l ?? "").trim().toLowerCase();

const hashPw = (pw, salt = crypto.randomBytes(16).toString("hex")) => ({
  salt,
  hash: crypto.scryptSync(String(pw), salt, 64).toString("hex"),
});
const checkPw = (pw, u) => {
  const h = crypto.scryptSync(String(pw ?? ""), u.salt, 64);
  return crypto.timingSafeEqual(h, Buffer.from(u.hash, "hex"));
};
const DUMMY = hashPw("dummy-password");

async function secretOf(st) {
  if (process.env.AUTH_SECRET) return process.env.AUTH_SECRET;
  let s = await st.get("meta/secret");
  if (!s) {
    s = crypto.randomBytes(32).toString("hex");
    await st.set("meta/secret", s);
  }
  return s;
}
async function sign(st, u) {
  const p = Buffer.from(JSON.stringify({ l: u.login, v: u.v || 0, e: Date.now() + TOKEN_DAYS * 864e5 })).toString("base64url");
  const sig = crypto.createHmac("sha256", await secretOf(st)).update(p).digest("base64url");
  return p + "." + sig;
}
async function verify(st, req) {
  const t = (req.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  const [p, sig] = t.split(".");
  if (!p || !sig) return null;
  const good = crypto.createHmac("sha256", await secretOf(st)).update(p).digest("base64url");
  if (sig.length !== good.length || !crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(good))) return null;
  let d;
  try { d = JSON.parse(Buffer.from(p, "base64url").toString()); } catch { return null; }
  if (!d || d.e < Date.now()) return null;
  const u = await st.get("users/" + d.l, { type: "json" });
  if (!u || (u.v || 0) !== d.v) return null;
  return u;
}
async function userCount(st) {
  const { blobs } = await st.list({ prefix: "users/" });
  return blobs.length;
}
const allowSignup = () => process.env.ALLOW_SIGNUP === "true";
function validNew(login, password) {
  if (!LOGIN_RE.test(login)) return "Логин: 3–32 латинские буквы, цифры, точка, дефис или подчёркивание.";
  if (String(password ?? "").length < 6) return "Пароль должен быть не короче 6 символов.";
  if (String(password).length > 200) return "Слишком длинный пароль.";
  return null;
}
async function createUser(st, login, password, admin) {
  const u = { login, ...hashPw(password), admin: !!admin, v: 0, created: Date.now() };
  await st.setJSON("users/" + login, u);
  return u;
}

export default async (req) => {
  const st = getStore({ name: "budget", consistency: "strong" });
  const path = new URL(req.url).pathname.replace(/^\/api\/?/, "").replace(/\/$/, "");
  const m = req.method;
  let body = {};
  if (m !== "GET") {
    try { body = await req.json(); } catch { body = {}; }
  }
  try {
    if (path === "status" && m === "GET") {
      return json({ hasUsers: (await userCount(st)) > 0, allowSignup: allowSignup() });
    }

    if (path === "register" && m === "POST") {
      const first = (await userCount(st)) === 0;
      if (!first && !allowSignup()) return fail(403, "signup_closed", "Регистрация закрыта. Попросите владельца создать вам аккаунт.");
      const login = norm(body.login);
      const bad = validNew(login, body.password);
      if (bad) return fail(400, "invalid", bad);
      if (await st.get("users/" + login)) return fail(409, "taken", "Такой логин уже занят.");
      const u = await createUser(st, login, body.password, first);
      return json({ token: await sign(st, u), login, admin: u.admin });
    }

    if (path === "login" && m === "POST") {
      const login = norm(body.login);
      const u = LOGIN_RE.test(login) ? await st.get("users/" + login, { type: "json" }) : null;
      if (!u) { checkPw(body.password, DUMMY); return fail(401, "bad_credentials", "Неверный логин или пароль."); }
      if (u.lockUntil && u.lockUntil > Date.now()) return fail(429, "locked", "Слишком много неудачных попыток. Попробуйте через 15 минут.");
      if (!checkPw(body.password, u)) {
        u.fails = (u.fails || 0) + 1;
        if (u.fails >= MAX_FAILS) { u.lockUntil = Date.now() + LOCK_MS; u.fails = 0; }
        await st.setJSON("users/" + login, u);
        return fail(401, "bad_credentials", "Неверный логин или пароль.");
      }
      if (u.fails || u.lockUntil) { u.fails = 0; u.lockUntil = 0; await st.setJSON("users/" + login, u); }
      return json({ token: await sign(st, u), login, admin: !!u.admin });
    }

    const me = await verify(st, req);
    if (!me) return fail(401, "unauthorized", "Сеанс истёк. Войдите снова.");

    if (path === "me" && m === "GET") return json({ login: me.login, admin: !!me.admin });

    if (path === "data" && m === "GET") {
      const d = await st.get("data/" + me.login, { type: "json" });
      return json(d || { settings: null, tx: [], rev: 0 });
    }
    if (path === "data" && m === "PUT") {
      const cur = await st.get("data/" + me.login, { type: "json" });
      const rev = cur?.rev || 0;
      if (body.rev !== rev) return fail(409, "conflict", "Данные изменились на другом устройстве.");
      if (!body.settings || typeof body.settings !== "object" || !Array.isArray(body.tx)) return fail(400, "invalid", "Неверные данные.");
      const d = { settings: body.settings, tx: body.tx, rev: rev + 1, updated: Date.now() };
      if (JSON.stringify(d).length > MAX_DATA) return fail(413, "too_big", "Слишком много данных.");
      await st.setJSON("data/" + me.login, d);
      return json({ rev: d.rev });
    }

    if (path === "password" && m === "POST") {
      if (!checkPw(body.old, me)) return fail(403, "wrong_password", "Текущий пароль неверный.");
      const bad = validNew(me.login, body.password);
      if (bad) return fail(400, "invalid", bad);
      Object.assign(me, hashPw(body.password));
      me.v = (me.v || 0) + 1;
      await st.setJSON("users/" + me.login, me);
      return json({ token: await sign(st, me) });
    }

    if (path.startsWith("admin/")) {
      if (!me.admin) return fail(403, "forbidden", "Это может только владелец сайта.");
      if (path === "admin/users" && m === "GET") {
        const { blobs } = await st.list({ prefix: "users/" });
        const users = [];
        for (const b of blobs) {
          const u = await st.get(b.key, { type: "json" });
          if (u) users.push({ login: u.login, admin: !!u.admin, created: u.created });
        }
        users.sort((a, b) => (b.admin - a.admin) || a.created - b.created);
        return json({ users });
      }
      if (path === "admin/users" && m === "POST") {
        const login = norm(body.login);
        const bad = validNew(login, body.password);
        if (bad) return fail(400, "invalid", bad);
        if (await st.get("users/" + login)) return fail(409, "taken", "Такой логин уже занят.");
        await createUser(st, login, body.password, false);
        return json({ ok: true });
      }
      if (path === "admin/reset" && m === "POST") {
        const login = norm(body.login);
        const u = await st.get("users/" + login, { type: "json" });
        if (!u) return fail(404, "not_found", "Нет такого аккаунта.");
        const bad = validNew(login, body.password);
        if (bad) return fail(400, "invalid", bad);
        Object.assign(u, hashPw(body.password), { v: (u.v || 0) + 1, fails: 0, lockUntil: 0 });
        await st.setJSON("users/" + login, u);
        return json({ ok: true });
      }
      if (path === "admin/delete" && m === "POST") {
        const login = norm(body.login);
        if (login === me.login) return fail(400, "self", "Свой аккаунт удалить нельзя.");
        await st.delete("users/" + login);
        await st.delete("data/" + login);
        return json({ ok: true });
      }
    }
    return fail(404, "not_found", "Нет такого адреса.");
  } catch (e) {
    console.error(e);
    return fail(500, "server", "Ошибка сервера. Попробуйте ещё раз.");
  }
};

export const config = { path: "/api/*" };
