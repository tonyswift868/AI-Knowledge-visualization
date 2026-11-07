// Keep decimal amounts exact, including sub-cent calls and Decimal exponent notation.
function decimal(amount: string) {
  const [mantissa, exponent = "0"] = amount.toLowerCase().split("e")
  const [integer, fraction = ""] = mantissa.split(".")
  const scale = fraction.length - Number(exponent)
  const units = BigInt(integer + fraction)
  return { units: scale < 0 ? units * 10n ** BigInt(-scale) : units, scale: Math.max(0, scale) }
}

export function addMoney(left: string, right: string) {
  const a = decimal(left), b = decimal(right)
  const scale = Math.max(a.scale, b.scale)
  const units = a.units * 10n ** BigInt(scale - a.scale) + b.units * 10n ** BigInt(scale - b.scale)
  const negative = units < 0n
  const digits = (negative ? -units : units).toString().padStart(scale + 1, "0")
  return (negative ? "-" : "") + (scale ? `${digits.slice(0, -scale)}.${digits.slice(-scale)}` : digits)
}

export function money(amount: string, currency: string) {
  const value = decimal(amount)
  const negative = value.units < 0n
  const digits = (negative ? -value.units : value.units).toString().padStart(value.scale + 1, "0")
  const integer = value.scale ? digits.slice(0, -value.scale) : digits
  const fraction = (value.scale ? digits.slice(-value.scale) : "").replace(/0+$/, "").padEnd(2, "0")
  const symbol = currency === "CNY" ? "¥" : currency === "USD" ? "$" : currency + " "
  return `${symbol}${negative ? "-" : ""}${integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${fraction}`
}
