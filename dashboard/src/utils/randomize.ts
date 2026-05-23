// Generate by region
const MERCHANT_NAMES: Record<string, string[]> = {
  PK: ["Zara Mart", "Al-Fatah Superstore", "Metro Cash & Carry PK", "Carrefour Lahore", "HBL Traders", "Nisa Retail", "QMobile Shop", "Punjab Bakers"],
  AE: ["Al Noor Trading", "Carrefour Dubai", "Emirates Retail", "Lulu Hypermarket", "Al Maya Group"],
  SA: ["Al Othaim Markets", "Panda Retail", "Bin Dawood", "Danube Supermarkets"],
  EG: ["Carrefour Egypt", "Kheir Zaman", "Seoudi Markets", "Metro Markets EG"],
  BH: ["LuLu Bahrain", "Al Osra Supermarket", "The Sultan Center BH"],
}

const CURRENCIES: Record<string, string> = { PK: "PKR", AE: "AED", SA: "SAR", EG: "EGP", BH: "BHD" }

const MCC_CODES = [
  { code: "5411", label: "Grocery Stores" },
  { code: "5812", label: "Eating Places" },
  { code: "5912", label: "Drug Stores" },
  { code: "5541", label: "Service Stations" },
  { code: "5999", label: "Retail Stores" },
  { code: "5651", label: "Family Clothing" },
]

const FIRST_NAMES = ["Ahmed", "Sara", "Omar", "Fatima", "Ali", "Hana", "Khalid", "Nour"]
const LAST_NAMES = ["Al-Rashid", "Khan", "Hassan", "Ibrahim", "Malik", "Qureshi", "Al-Farouk", "Yousuf"]

export function randomRegion(): string {
  return ["PK", "AE", "SA", "EG", "BH"][Math.floor(Math.random() * 5)]
}

export function randomMerchantName(region?: string): string {
  const r = region ?? randomRegion()
  const list = MERCHANT_NAMES[r] ?? MERCHANT_NAMES.PK
  return list[Math.floor(Math.random() * list.length)]
}

export function randomMcc(): string {
  return MCC_CODES[Math.floor(Math.random() * MCC_CODES.length)].code
}

export function randomPersonName(): string {
  return `${FIRST_NAMES[Math.floor(Math.random() * FIRST_NAMES.length)]} ${LAST_NAMES[Math.floor(Math.random() * LAST_NAMES.length)]}`
}

export function randomCurrency(region?: string): string {
  return CURRENCIES[region ?? randomRegion()] ?? "PKR"
}

export function randomAmount(min = 100, max = 50000): number {
  return Math.round((Math.random() * (max - min) + min) * 100) / 100
}

export function randomIban(region: string = "PK"): string {
  // Generate realistic-format IBAN (NOTE: must be replaced with a real account IBAN for payments to work)
  const digits = Array.from({ length: 16 }, () => Math.floor(Math.random() * 10)).join("")
  const check = String(Math.floor(Math.random() * 90 + 10))
  if (region === "PK") return `PK${check}SCBL${digits}`
  if (region === "AE") return `AE${check}033${digits.slice(0, 16)}`
  if (region === "SA") return `SA${check}10${digits}`
  if (region === "EG") return `EG${check}0011${digits.slice(0, 16)}`
  if (region === "BH") return `BH${check}BINO${digits.slice(0, 14)}`
  return `PK${check}SCBL${digits}`
}

export function randomUlid(): string {
  return Array.from({ length: 26 }, () => "0123456789ABCDEFGHJKMNPQRSTVWXYZ"[Math.floor(Math.random() * 32)]).join("")
}

export function randomUuid(): string {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0
    return (c === "x" ? r : r & 0x3 | 0x8).toString(16)
  })
}

export function randomRef(prefix = "REF"): string {
  return `${prefix}-${Date.now().toString(36).toUpperCase()}-${Math.floor(Math.random() * 9999).toString().padStart(4, "0")}`
}

export function randomFutureDate(daysAhead = 365): string {
  const d = new Date()
  d.setDate(d.getDate() + Math.floor(Math.random() * daysAhead + 30))
  d.setMilliseconds(0); d.setSeconds(0)
  return d.toISOString().slice(0, 16)  // for datetime-local input
}
