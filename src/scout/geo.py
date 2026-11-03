"""Countries, cities and currencies, for reading locations out of resumes and postings. Pure data."""
from __future__ import annotations

import re
from functools import lru_cache

# code -> (name, currency)
COUNTRIES: dict[str, tuple[str, str]] = {
    "IN": ("India", "INR"), "US": ("United States", "USD"), "GB": ("United Kingdom", "GBP"), "CA": ("Canada", "CAD"),
    "AU": ("Australia", "AUD"), "NZ": ("New Zealand", "NZD"), "SG": ("Singapore", "SGD"), "MY": ("Malaysia", "MYR"),
    "PH": ("Philippines", "PHP"), "ID": ("Indonesia", "IDR"), "AE": ("United Arab Emirates", "AED"),
    "SA": ("Saudi Arabia", "SAR"), "QA": ("Qatar", "QAR"), "DE": ("Germany", "EUR"), "AT": ("Austria", "EUR"),
    "CH": ("Switzerland", "CHF"), "FR": ("France", "EUR"), "NL": ("Netherlands", "EUR"), "IE": ("Ireland", "EUR"),
    "ES": ("Spain", "EUR"), "IT": ("Italy", "EUR"), "SE": ("Sweden", "SEK"), "PL": ("Poland", "PLN"),
    "PT": ("Portugal", "EUR"), "BE": ("Belgium", "EUR"), "DK": ("Denmark", "DKK"), "NO": ("Norway", "NOK"),
    "FI": ("Finland", "EUR"), "CZ": ("Czech Republic", "CZK"), "JP": ("Japan", "JPY"), "KR": ("South Korea", "KRW"),
    "HK": ("Hong Kong", "HKD"), "CN": ("China", "CNY"), "TW": ("Taiwan", "TWD"), "VN": ("Vietnam", "VND"),
    "TH": ("Thailand", "THB"), "BD": ("Bangladesh", "BDT"), "PK": ("Pakistan", "PKR"), "LK": ("Sri Lanka", "LKR"),
    "IL": ("Israel", "ILS"), "TR": ("Turkey", "TRY"), "EG": ("Egypt", "EGP"), "NG": ("Nigeria", "NGN"),
    "KE": ("Kenya", "KES"), "ZA": ("South Africa", "ZAR"), "BR": ("Brazil", "BRL"), "MX": ("Mexico", "MXN"),
    "AR": ("Argentina", "ARS"), "CL": ("Chile", "CLP"), "CO": ("Colombia", "COP"),
}

ALIASES = {
    "india": "IN", "bharat": "IN", "usa": "US", "us": "US", "u.s.": "US", "u.s.a.": "US", "united states of america": "US",
    "america": "US", "uk": "GB", "u.k.": "GB", "britain": "GB", "great britain": "GB", "england": "GB", "scotland": "GB",
    "wales": "GB", "uae": "AE", "ksa": "SA", "holland": "NL", "the netherlands": "NL", "korea": "KR", "czechia": "CZ",
    "deutschland": "DE",
}

# city (lowercase, with common aliases) -> (display name, country code)
CITIES: dict[str, tuple[str, str]] = {
    "bengaluru": ("Bengaluru", "IN"), "bangalore": ("Bengaluru", "IN"), "pune": ("Pune", "IN"), "mumbai": ("Mumbai", "IN"),
    "bombay": ("Mumbai", "IN"), "navi mumbai": ("Mumbai", "IN"), "thane": ("Mumbai", "IN"), "hyderabad": ("Hyderabad", "IN"),
    "secunderabad": ("Hyderabad", "IN"), "chennai": ("Chennai", "IN"), "madras": ("Chennai", "IN"),
    "delhi": ("Delhi NCR", "IN"), "new delhi": ("Delhi NCR", "IN"), "ncr": ("Delhi NCR", "IN"), "delhi ncr": ("Delhi NCR", "IN"),
    "gurugram": ("Delhi NCR", "IN"), "gurgaon": ("Delhi NCR", "IN"), "noida": ("Delhi NCR", "IN"),
    "greater noida": ("Delhi NCR", "IN"), "faridabad": ("Delhi NCR", "IN"), "ghaziabad": ("Delhi NCR", "IN"),
    "kolkata": ("Kolkata", "IN"), "calcutta": ("Kolkata", "IN"), "ahmedabad": ("Ahmedabad", "IN"),
    "gandhinagar": ("Ahmedabad", "IN"), "kochi": ("Kochi", "IN"), "cochin": ("Kochi", "IN"), "jaipur": ("Jaipur", "IN"),
    "chandigarh": ("Chandigarh", "IN"), "mohali": ("Chandigarh", "IN"), "indore": ("Indore", "IN"),
    "coimbatore": ("Coimbatore", "IN"), "thiruvananthapuram": ("Thiruvananthapuram", "IN"),
    "trivandrum": ("Thiruvananthapuram", "IN"), "nagpur": ("Nagpur", "IN"), "lucknow": ("Lucknow", "IN"),
    "bhubaneswar": ("Bhubaneswar", "IN"), "vadodara": ("Vadodara", "IN"), "surat": ("Surat", "IN"),
    "visakhapatnam": ("Visakhapatnam", "IN"), "mysuru": ("Mysuru", "IN"), "mysore": ("Mysuru", "IN"),
    "mangaluru": ("Mangaluru", "IN"), "mangalore": ("Mangaluru", "IN"), "goa": ("Goa", "IN"),
    "san francisco": ("San Francisco", "US"), "bay area": ("San Francisco", "US"), "new york": ("New York", "US"),
    "nyc": ("New York", "US"), "seattle": ("Seattle", "US"), "austin": ("Austin", "US"), "boston": ("Boston", "US"),
    "chicago": ("Chicago", "US"), "los angeles": ("Los Angeles", "US"), "san jose": ("San Jose", "US"),
    "mountain view": ("Mountain View", "US"), "palo alto": ("Palo Alto", "US"), "denver": ("Denver", "US"),
    "atlanta": ("Atlanta", "US"), "washington dc": ("Washington", "US"), "london": ("London", "GB"),
    "manchester": ("Manchester", "GB"), "edinburgh": ("Edinburgh", "GB"), "cambridge uk": ("Cambridge", "GB"),
    "dublin": ("Dublin", "IE"), "berlin": ("Berlin", "DE"), "munich": ("Munich", "DE"), "hamburg": ("Hamburg", "DE"),
    "frankfurt": ("Frankfurt", "DE"), "amsterdam": ("Amsterdam", "NL"), "paris": ("Paris", "FR"),
    "madrid": ("Madrid", "ES"), "barcelona": ("Barcelona", "ES"), "lisbon": ("Lisbon", "PT"), "zurich": ("Zurich", "CH"),
    "stockholm": ("Stockholm", "SE"), "warsaw": ("Warsaw", "PL"), "toronto": ("Toronto", "CA"),
    "vancouver": ("Vancouver", "CA"), "montreal": ("Montreal", "CA"), "sydney": ("Sydney", "AU"),
    "melbourne": ("Melbourne", "AU"), "singapore": ("Singapore", "SG"), "dubai": ("Dubai", "AE"),
    "abu dhabi": ("Abu Dhabi", "AE"), "riyadh": ("Riyadh", "SA"), "doha": ("Doha", "QA"), "tokyo": ("Tokyo", "JP"),
    "seoul": ("Seoul", "KR"), "hong kong": ("Hong Kong", "HK"), "kuala lumpur": ("Kuala Lumpur", "MY"),
    "jakarta": ("Jakarta", "ID"), "manila": ("Manila", "PH"), "tel aviv": ("Tel Aviv", "IL"),
    "sao paulo": ("Sao Paulo", "BR"), "mexico city": ("Mexico City", "MX"), "nairobi": ("Nairobi", "KE"),
    "lagos": ("Lagos", "NG"), "cape town": ("Cape Town", "ZA"), "dhaka": ("Dhaka", "BD"), "karachi": ("Karachi", "PK"),
    "lahore": ("Lahore", "PK"), "colombo": ("Colombo", "LK"),
}

# US states / Indian states often written instead of the country
REGIONS: dict[str, str] = {
    "california": "US", "texas": "US", "washington": "US", "massachusetts": "US", "illinois": "US", "colorado": "US",
    "georgia": "US", "new jersey": "US", " ca,": "US", " ny": "US", " tx": "US", " wa": "US",
    "maharashtra": "IN", "karnataka": "IN", "telangana": "IN", "tamil nadu": "IN", "haryana": "IN",
    "uttar pradesh": "IN", "gujarat": "IN", "kerala": "IN", "west bengal": "IN", "rajasthan": "IN",
    "ontario": "CA", "british columbia": "CA", "new south wales": "AU", "victoria": "AU",
}


def country_code(text: str | None) -> str | None:
    """'India' / 'in' / 'IN' / 'uae' -> 'IN' / 'AE'; None when unknown."""
    key = " ".join((text or "").split()).strip(" .,").lower()
    if not key:
        return None
    if key.upper() in COUNTRIES:
        return key.upper()
    if key in ALIASES:
        return ALIASES[key]
    for code, (name, _) in COUNTRIES.items():
        if key == name.lower():
            return code
    return None


def country_name(code: str | None) -> str:
    return COUNTRIES[code][0] if code in COUNTRIES else ""


def currency(code: str | None) -> str | None:
    return COUNTRIES[code][1] if code in COUNTRIES else None


@lru_cache(maxsize=1)
def _city_pattern() -> re.Pattern:
    names = sorted(CITIES, key=len, reverse=True)
    return re.compile(r"(?<![\w-])(" + "|".join(re.escape(n) for n in names) + r")(?![\w-])", re.I)


@lru_cache(maxsize=1)
def _country_pattern() -> re.Pattern:
    names = [n.lower() for n, _ in COUNTRIES.values()] + [a for a in ALIASES if len(a) > 2]
    names = sorted(set(names), key=len, reverse=True)
    return re.compile(r"(?<![\w-])(" + "|".join(re.escape(n) for n in names) + r")(?![\w-])", re.I)


def find_cities(text: str) -> list[tuple[str, str]]:
    """[(display city, country code)] in order of appearance, without repeats."""
    seen, out = set(), []
    for m in _city_pattern().finditer(text or ""):
        city = CITIES[m.group(1).lower()]
        if city not in seen:
            seen.add(city)
            out.append(city)
    return out


def find_country(text: str) -> str | None:
    """The first country named in the text, else the country of the first city or region."""
    m = _country_pattern().search(text or "")
    if m:
        return country_code(m.group(1))
    cities = find_cities(text)
    if cities:
        return cities[0][1]
    low = f" {(text or '').lower()} "
    for region, code in REGIONS.items():
        if region in low:
            return code
    return None


def canonical_city(name: str) -> str | None:
    hit = CITIES.get(" ".join((name or "").split()).lower())
    return hit[0] if hit else None


_CITY_COUNTRY = {display: code for display, code in CITIES.values()}


def city_country(display: str) -> str | None:
    return _CITY_COUNTRY.get(display)
