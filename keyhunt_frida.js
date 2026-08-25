// Hook CommonCrypto AES/KDF entry points in WeChat; report keys.
// 适用于微信 4.1.10+（进程内存中不再缓存 x'<hexkey>' 字符串的版本）：
// 派生后的 per-DB raw key 只在加解密瞬间经过 CommonCrypto。
//   CCCrypt(op,alg,options,key,keyLength,iv,...)              key=args[3] len=args[4]
//   CCCryptorCreate(op,alg,options,key,keyLength,iv,...)      key=args[3] len=args[4]
//   CCCryptorCreateWithMode(op,mode,alg,pad,iv,key,keyLen,..) key=args[5] len=args[6]
//   CCKeyDerivationPBKDF(alg,password,pwLen,salt,saltLen,prf,rounds,derivedKey,derivedKeyLen)
//     — SQLCipher4 mac key 派生：password=raw enc_key(32B), rounds=2, prf=SHA512
'use strict';

const seen = new Set();

function toHex(buf) {
  return Array.from(new Uint8Array(buf), b => ('0' + b.toString(16)).slice(-2)).join('');
}

function logKey(tag, key, len) {
  try {
    if (len !== 32) return;
    if (key.isNull()) return;
    const hex = toHex(key.readByteArray(32));
    if (seen.has(hex)) return;
    seen.add(hex);
    send('KEY32 ' + tag + ' ' + hex);
  } catch (e) { /* unreadable pointer — skip */ }
}

function resolve(name) {
  try { if (Module.findGlobalExportByName) { const a = Module.findGlobalExportByName(name); if (a) return a; } } catch (e) {}
  try { if (Module.getGlobalExportByName) return Module.getGlobalExportByName(name); } catch (e) {}
  try { return Module.getExportByName(null, name); } catch (e) {}
  return null;
}

let hooked = 0;
for (const name of ['CCCrypt', 'CCCryptorCreate', 'CCCryptorCreateWithMode']) {
  const addr = resolve(name);
  if (addr === null) { send('MISS ' + name); continue; }
  const keyIdx = name === 'CCCryptorCreateWithMode' ? 5 : 3;
  const lenIdx = keyIdx + 1;
  Interceptor.attach(addr, {
    onEnter(args) { logKey(name, args[keyIdx], args[lenIdx].toInt32()); }
  });
  hooked++;
}

const pbkdf = resolve('CCKeyDerivationPBKDF');
if (pbkdf !== null) {
  Interceptor.attach(pbkdf, {
    onEnter(args) {
      this.pw = args[1];
      this.pwLen = args[2].toInt32();
      this.salt = args[3];
      this.saltLen = args[4].toInt32();
      this.prf = args[5].toInt32();
      this.rounds = args[6].toInt32();
      this.dk = args[7];
      // 9th arg (derivedKeyLen) lives on the stack at entry on arm64
      try { this.dkLen = this.context.sp.readU64().toNumber(); } catch (e) { this.dkLen = -1; }
      try {
        const pwHex = toHex(this.pw.readByteArray(Math.min(Math.max(this.pwLen, 0), 128)));
        const saltHex = toHex(this.salt.readByteArray(Math.min(Math.max(this.saltLen, 0), 64)));
        send('PBKDF pw=' + pwHex + ' pwLen=' + this.pwLen +
             ' salt=' + saltHex + ' prf=' + this.prf + ' rounds=' + this.rounds +
             ' dkLen=' + this.dkLen);
      } catch (e) {}
    },
    onLeave(retval) {
      try {
        if (retval.toInt32() === 0 && !this.dk.isNull()) {
          const n = this.dkLen > 0 && this.dkLen <= 128 ? this.dkLen : 64;
          send('PBKDFDK dk=' + toHex(this.dk.readByteArray(n)));
        }
      } catch (e) {}
    }
  });
  hooked++;
} else {
  send('MISS CCKeyDerivationPBKDF');
}

send('ARMED ' + hooked + ' hooks');
