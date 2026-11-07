/**
 * RFC 4122 v4 UUID, dependency-free.
 *
 * `crypto.randomUUID()` is not guaranteed on every Hermes build we ship to, so we derive the
 * 128 bits from `Math.random`. Good enough for event-idempotency keys and anonymous subject
 * ids — neither needs cryptographic uniqueness, just collision-resistance in practice.
 */
export function uuidv4(): string {
  const bytes = new Uint8Array(16);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = Math.floor(Math.random() * 256);
  }
  // RFC 4122 §4.4: version (4 bits) = 4, variant (2 bits) = 10.
  bytes[6] = (bytes[6] ?? 0) & 0x0f;
  bytes[6] = (bytes[6] ?? 0) | 0x40;
  bytes[8] = (bytes[8] ?? 0) & 0x3f;
  bytes[8] = (bytes[8] ?? 0) | 0x80;

  const hex: string[] = [];
  for (let i = 0; i < bytes.length; i++) {
    const b = bytes[i] ?? 0;
    hex.push(b.toString(16).padStart(2, "0"));
  }
  return (
    hex.slice(0, 4).join("") +
    "-" +
    hex.slice(4, 6).join("") +
    "-" +
    hex.slice(6, 8).join("") +
    "-" +
    hex.slice(8, 10).join("") +
    "-" +
    hex.slice(10, 16).join("")
  );
}
