"""
find_demo_addresses.py — Personne 1
Trouve 3 adresses contrastées pour la démo et affiche leur blklot
(à transmettre à la Personne 3 pour y rattacher les avis fictifs).

  python find_demo_addresses.py
"""
from data import soql, years_ago, get_building, q


def problematic(n=5):
    """Bâtiments résidentiels avec le plus d'infractions DBI 'unsafe' ou actives récentes."""
    rows = soql("violations",
                select="block,lot,street_number,street_name,street_suffix,count(*) as n",
                where=f"date_filed >= '{years_ago(3)}' AND status='active'",
                group="block,lot,street_number,street_name,street_suffix",
                order="n DESC", limit=n)
    return [f"{r['street_number']} {r['street_name']} {r.get('street_suffix', '')}".strip() for r in rows]


def well_kept(n=5):
    """Immeubles de 10+ logements, sans plainte DBI sur 10 ans."""
    rolls = soql("tax_rolls",
                 select="block,lot,property_location,number_of_units",
                 where="closed_roll_year='2025' AND number_of_units >= 10 AND number_of_units <= 60",
                 limit=40)
    out = []
    for r in rolls:
        c = soql("dbi_complaints", select="count(*) as n",
                 where=f"block='{q(r['block'])}' AND lot='{q(r['lot'])}' "
                       f"AND date_filed >= '{years_ago(10)}'")
        if int(c[0]["n"]) == 0:
            # adresse via le dataset des parcelles
            p = soql("parcels", select="from_address_num,street_name,street_type",
                     where=f"block_num='{q(r['block'])}' AND lot_num='{q(r['lot'])}'", limit=1)
            if p and p[0].get("from_address_num") and p[0]["from_address_num"] != "0":
                out.append(f"{p[0]['from_address_num']} {p[0]['street_name']} {p[0].get('street_type', '')}".strip())
        if len(out) >= n:
            break
    return out


def ellis_blocks(n=5):
    """Blocs de rue avec le plus d'évictions Ellis Act / owner move-in sur 5 ans."""
    rows = soql("evictions", select="address,count(*) as n",
                where=f"(ellis_act_withdrawal=true OR owner_move_in=true) "
                      f"AND file_date >= '{years_ago(5)}'",
                group="address", order="n DESC", limit=n)
    out = []
    for r in rows:  # "3500 Block Of Judah Street" -> une vraie adresse du bloc
        parts = r["address"].upper().split(" BLOCK OF ")
        if len(parts) != 2:
            continue
        hundred, street = int(parts[0]), parts[1].rsplit(" ", 1)[0]
        a = soql("addresses", select="address",
                 where=f"street_name='{q(street)}' AND address_number between "
                       f"'{hundred}' and '{hundred + 99}'", limit=1)
        if a:
            out.append(a[0]["address"])
    return out


def show(label, addresses):
    print(f"\n=== {label}")
    for a in addresses:
        try:
            b = get_building(a)
            print(f"  {b['matched_address']:<28} blklot={b['blklot']:<10} "
                  f"année={b['profile']['year_built']} logements={b['profile']['units']} "
                  f"stats={b['stats']}")
        except Exception as e:
            print(f"  {a}: {e}")


if __name__ == "__main__":
    show("PROBLÉMATIQUES (infractions actives récentes)", problematic())
    show("BIEN TENUS (10+ logements, 0 plainte en 10 ans)", well_kept())
    show("BLOCS AVEC ÉVICTIONS ELLIS / OWNER MOVE-IN", ellis_blocks())
