"""Commute distance and travel time calculation from home to appointment facilities.

Calculates distance and travel times both by car (during peak hours) and by
public transit (subway, train, feeder buses, and walking).
Uses the user's home coordinates configured in .env (HOME_COORDINATES).
If home coordinates are not configured, no calculation is performed.
Also predisposed for future distance/travel-time filtering criteria.
"""
from __future__ import annotations

import functools
import json
import logging
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.config import BASE_DIR

log = logging.getLogger(__name__)

DEFAULT_GEOCACHE_PATH = BASE_DIR / "data" / "geocache.json"


@dataclass
class CommuteInfo:
    """Calculated commute information from user's home to appointment facility."""
    distance_km: float                  # Estimated road driving distance in km
    straight_km: float                  # Geodesic straight-line distance in km
    car_minutes: int                    # Driving duration in minutes (peak traffic)
    car_text: str                       # e.g. "🚗 24 min in auto (14.2 km)"
    transit_minutes: int                # Public transit duration in minutes
    transit_route: str                  # e.g. "M2 + M1" or "M3" or "a piedi"
    transit_text: str                   # e.g. "🚇 38 min con trasporto pubblico (M2 + M1)"
    walking_minutes: int | None = None  # Walking duration in min (if pedestrian distance)
    is_walking: bool = False            # True if within pedestrian walking distance (<= 850m)
    summary_text: str = ""              # Formatted summary for notifications
    dest_coords: tuple[float, float] | None = None
    origin_coords: tuple[float, float] | None = None
    facility_name: str = ""


# ==============================================================================
# Canonical coordinates of subway & suburban train stations (Milano, Brescia, FS Hubs)
# ==============================================================================
TRANSIT_STATIONS: dict[str, tuple[float, float]] = {
    # Milano M2
    "Gessate (M2)": (45.5422, 9.4375),
    "Cascina Antonietta (M2)": (45.5358, 9.4208),
    "Gorgonzola (M2)": (45.5311, 9.4042),
    "Villa Pompea (M2)": (45.5248, 9.3888),
    "Bussero (M2)": (45.5254, 9.3758),
    "Cassina de' Pecchi (M2)": (45.5222, 9.3625),
    "Villa Fiorita (M2)": (45.5225, 9.3458),
    "Cernusco sul Naviglio (M2)": (45.5228, 9.3308),
    "Cascina Burrona (M2)": (45.5192, 9.3003),
    "Vimodrone (M2)": (45.5153, 9.2872),
    "Cascina Gobba (M2)": (45.5111, 9.2608),
    "Crescenzago (M2)": (45.5036, 9.2483),
    "Cimiano (M2)": (45.4967, 9.2411),
    "Udine (M2)": (45.4917, 9.2369),
    "Lambrate FS (M2)": (45.4850, 9.2361),
    "Piola (M2)": (45.4803, 9.2253),
    "Loreto (M1/M2)": (45.4853, 9.2161),
    "Caiazzo (M2)": (45.4842, 9.2114),
    "Centrale FS (M2/M3)": (45.4850, 9.2033),
    "Gioia (M2)": (45.4844, 9.1953),
    "Garibaldi FS (M2/M5)": (45.4839, 9.1883),
    "Moscova (M2)": (45.4778, 9.1856),
    "Lanza (M2)": (45.4719, 9.1825),
    "Cadorna FN (M1/M2)": (45.4683, 9.1764),
    "Sant'Ambrogio (M2/M4)": (45.4619, 9.1736),
    "Sant'Agostino (M2)": (45.4583, 9.1719),
    "Porta Genova FS (M2)": (45.4528, 9.1700),
    "Romolo (M2)": (45.4439, 9.1678),
    "Famagosta (M2)": (45.4372, 9.1689),
    "Piazza Abbiategrasso (M2)": (45.4297, 9.1778),
    "Assago Milanofiori Nord (M2)": (45.4122, 9.1481),
    "Assago Milanofiori Forum (M2)": (45.4022, 9.1436),
    "Cologno Sud (M2)": (45.5231, 9.2708),
    "Cologno Centro (M2)": (45.5283, 9.2783),
    "Cologno Nord (M2)": (45.5331, 9.2886),

    # Milano M1
    "Sesto 1° Maggio FS (M1)": (45.5414, 9.2381),
    "Sesto Rondò (M1)": (45.5342, 9.2319),
    "Sesto Marelli (M1)": (45.5275, 9.2258),
    "Villa San Giovanni (M1)": (45.5186, 9.2208),
    "Precotto (M1)": (45.5122, 9.2178),
    "Gorla (M1)": (45.5064, 9.2189),
    "Turro (M1)": (45.4983, 9.2208),
    "Rovereto (M1)": (45.4925, 9.2178),
    "Pasteur (M1)": (45.4881, 9.2153),
    "Porta Venezia (M1)": (45.4744, 9.2056),
    "Palestro (M1)": (45.4711, 9.2017),
    "San Babila (M1/M4)": (45.4667, 9.1972),
    "Duomo (M1/M3)": (45.4647, 9.1894),
    "Cordusio (M1)": (45.4656, 9.1864),
    "Cairoli (M1)": (45.4683, 9.1822),
    "Conciliazione (M1)": (45.4678, 9.1667),
    "Pagano (M1)": (45.4675, 9.1586),
    "Buonarroti (M1)": (45.4697, 9.1517),
    "Amendola (M1)": (45.4736, 9.1483),
    "Lotto (M1/M5)": (45.4786, 9.1428),
    "QT8 (M1)": (45.4839, 9.1367),
    "Lampugnano (M1)": (45.4897, 9.1275),
    "Uruguay (M1)": (45.4967, 9.1233),
    "Bonola (M1)": (45.5008, 9.1242),
    "San Leonardo (M1)": (45.5014, 9.1036),
    "Molino Dorino (M1)": (45.5047, 9.0936),
    "Pero (M1)": (45.5097, 9.0839),
    "Rho Fieramilano (M1)": (45.5200, 9.0869),
    "Wagner (M1)": (45.4653, 9.1558),
    "De Angeli (M1)": (45.4642, 9.1489),
    "Gambara (M1)": (45.4628, 9.1417),
    "Bande Nere (M1)": (45.4608, 9.1350),
    "Primaticcio (M1)": (45.4594, 9.1283),
    "Inganni (M1)": (45.4578, 9.1208),
    "Bisceglie (M1)": (45.4556, 9.1125),

    # Milano M3
    "Comasina (M3)": (45.5286, 9.1625),
    "Affori FN (M3)": (45.5217, 9.1678),
    "Affori Centro (M3)": (45.5139, 9.1742),
    "Dergano (M3)": (45.5056, 9.1794),
    "Maciachini (M3)": (45.4975, 9.1856),
    "Zara (M3/M5)": (45.4925, 9.1925),
    "Sondrio (M3)": (45.4894, 9.2008),
    "Repubblica (M3)": (45.4783, 9.1978),
    "Turati (M3)": (45.4744, 9.1947),
    "Montenapoleone (M3)": (45.4703, 9.1925),
    "Missori (M3)": (45.4608, 9.1886),
    "Crocetta (M3)": (45.4558, 9.1947),
    "Porta Romana (M3)": (45.4514, 9.2028),
    "Lodi TIBB (M3)": (45.4481, 9.2103),
    "Brenta (M3)": (45.4439, 9.2189),
    "Corvetto (M3)": (45.4403, 9.2247),
    "Porto di Mare (M3)": (45.4356, 9.2333),
    "Rogoredo FS (M3)": (45.4331, 9.2389),
    "San Donato (M3)": (45.4278, 9.2561),

    # Milano M4
    "San Cristoforo FS (M4)": (45.4428, 9.1286),
    "Segneri (M4)": (45.4478, 9.1353),
    "Gelsomini (M4)": (45.4511, 9.1417),
    "Frattini (M4)": (45.4539, 9.1469),
    "Tolstoj (M4)": (45.4564, 9.1539),
    "Bolivar (M4)": (45.4589, 9.1586),
    "California (M4)": (45.4606, 9.1633),
    "Coni Zugna (M4)": (45.4608, 9.1689),
    "De Amicis (M4)": (45.4597, 9.1794),
    "Vetra (M4)": (45.4578, 9.1836),
    "Santa Sofia (M4)": (45.4558, 9.1883),
    "Sforza-Policlinico (M4)": (45.4589, 9.1947),
    "Tricolore (M4)": (45.4678, 9.2056),
    "Dateo (M4)": (45.4683, 9.2158),
    "Susa (M4)": (45.4678, 9.2242),
    "Argonne (M4)": (45.4678, 9.2325),
    "Stazione Forlanini (M4)": (45.4650, 9.2417),
    "Repetti (M4)": (45.4619, 9.2494),
    "Linate Aeroporto (M4)": (45.4542, 9.2789),

    # Milano M5
    "Bignami (M5)": (45.5258, 9.2114),
    "Ponale (M5)": (45.5197, 9.2086),
    "Bicocca (M5)": (45.5147, 9.2056),
    "Ca' Granda (M5)": (45.5075, 9.1997),
    "Istria (M5)": (45.5014, 9.1956),
    "Marche (M5)": (45.4958, 9.1942),
    "Isola (M5)": (45.4875, 9.1903),
    "Monumentale (M5)": (45.4847, 9.1786),
    "Cenisio (M5)": (45.4878, 9.1706),
    "Gerusalemme (M5)": (45.4839, 9.1636),
    "Domodossola FN (M5)": (45.4808, 9.1583),
    "Tre Torri (M5)": (45.4764, 9.1539),
    "Portello (M5)": (45.4789, 9.1481),
    "Segesta (M5)": (45.4756, 9.1378),
    "San Siro Ippodromo (M5)": (45.4761, 9.1294),
    "San Siro Stadio (M5)": (45.4775, 9.1217),

    # Brescia Metro
    "Prealpino (Metro BS)": (45.5744, 10.2306),
    "Casazza (Metro BS)": (45.5642, 10.2294),
    "Mompiano (Metro BS)": (45.5583, 10.2297),
    "Europa (Metro BS)": (45.5542, 10.2292),
    "Ospedale (Metro BS)": (45.5564, 10.2294),
    "Marconi (Metro BS)": (45.5489, 10.2264),
    "San Faustino (Metro BS)": (45.5433, 10.2214),
    "Vittoria (Metro BS)": (45.5392, 10.2189),
    "Stazione FS (Metro BS)": (45.5333, 10.2139),
    "Bresciadue (Metro BS)": (45.5264, 10.2131),
    "Lamarmora (Metro BS)": (45.5206, 10.2108),
    "Volta (Metro BS)": (45.5186, 10.2197),
    "Poliambulanza (Metro BS)": (45.5189, 10.2289),
    "San Polo Parco (Metro BS)": (45.5181, 10.2403),
    "San Polo (Metro BS)": (45.5164, 10.2506),
    "Sanpolino (Metro BS)": (45.5128, 10.2608),
    "Sant'Eufemia-Buffalora (Metro BS)": (45.5103, 10.2736),

    # Lombardy Railway Hubs (FS / Trenord)
    "Monza FS": (45.5786, 9.2731),
    "Saronno FN": (45.6269, 9.0308),
    "Bergamo FS": (45.6908, 9.6708),
    "Brescia FS": (45.5333, 10.2139),
    "Como San Giovanni": (45.8089, 9.0736),
    "Pavia FS": (45.1889, 9.1439),
    "Lecco FS": (45.8564, 9.3917),
    "Varese FS": (45.8172, 8.8319),
    "Lodi FS": (45.3108, 9.4975),
    "Cremona FS": (45.1439, 10.0242),
    "Mantova FS": (45.1583, 10.7817),
    "Sondrio FS": (46.1689, 9.8708),
    "Busto Arsizio FS": (45.6119, 8.8617),
    "Gallarate FS": (45.5969, 8.7958),
    "Legnano FS": (45.5947, 8.9133),
    "Rho Fiera FS": (45.5200, 9.0869),
    "Treviglio FS": (45.5189, 9.5931),
    "Pioltello-Limito FS": (45.4858, 9.3283),
    "Voghera FS": (44.9964, 9.0119),
    "Vigevano FS": (45.3189, 8.8689),
}


# ==============================================================================
# Known Lombardy Hospital Facilities & Polyclinics (Instant Offline Geocoding)
# ==============================================================================
KNOWN_FACILITIES: dict[str, tuple[float, float, str]] = {
    # Milano Città
    "niguarda": (45.5108, 9.1889, "ASST Grande Ospedale Metropolitano Niguarda"),
    "ca' granda": (45.5108, 9.1889, "Ospedale Niguarda Ca' Granda"),
    "san raffaele": (45.5056, 9.2661, "IRCCS Ospedale San Raffaele"),
    "policlinico di milano": (45.4589, 9.1950, "Fondazione IRCCS Ca' Granda Ospedale Maggiore Policlinico"),
    "policlinico milano": (45.4589, 9.1950, "Fondazione IRCCS Ca' Granda Ospedale Maggiore Policlinico"),
    "policlinico ca' granda": (45.4589, 9.1950, "Fondazione IRCCS Ca' Granda Ospedale Maggiore Policlinico"),
    "policlinico ca granda": (45.4589, 9.1950, "Fondazione IRCCS Ca' Granda Ospedale Maggiore Policlinico"),
    "san paolo": (45.4339, 9.1558, "Ospedale San Paolo Milano"),
    "san carlo": (45.4708, 9.1089, "Ospedale San Carlo Borromeo Milano"),
    "sacco": (45.5197, 9.1242, "Ospedale Luigi Sacco Milano"),
    "fatebenefratelli": (45.4789, 9.1925, "Ospedale Fatebenefratelli Milano"),
    "buzzi": (45.4858, 9.1639, "Ospedale dei Bambini Vittore Buzzi Milano"),
    "gaetano pini": (45.4542, 9.1917, "ASST Gaetano Pini-CTO"),
    "pini": (45.4542, 9.1917, "ASST Gaetano Pini-CTO"),
    "tumori": (45.4744, 9.2278, "Istituto Nazionale dei Tumori Milano"),
    "besta": (45.4756, 9.2294, "Fondazione IRCCS Istituto Neurologico Carlo Besta"),
    "san giuseppe": (45.4628, 9.1722, "Ospedale San Giuseppe Milano"),
    "galeazzi": (45.5194, 9.0967, "IRCCS Ospedale Galeazzi - Sant'Ambrogio"),
    "sant'ambrogio": (45.5194, 9.0967, "IRCCS Galeazzi - Sant'Ambrogio"),
    "monzino": (45.4417, 9.2558, "Centro Cardiologico Monzino"),
    "melloni": (45.4678, 9.2158, "Presidio Ospedaliero Macedonio Melloni"),
    "oftalmico": (45.4778, 9.1917, "Ospedale Oftalmico Milano"),
    "auxologico": (45.4672, 9.1558, "Istituto Auxologico Italiano"),
    "marchese": (45.4542, 9.1917, "Presidio Ospedaliero CTO Pini"),

    # Hinterland Milano & Monza Brianza
    "policlinico san donato": (45.4194, 9.2683, "IRCCS Policlinico San Donato"),
    "san donato": (45.4194, 9.2683, "IRCCS Policlinico San Donato"),
    "humanitas": (45.3739, 9.1561, "IRCCS Istituto Clinico Humanitas Rozzano"),
    "multimedica": (45.5306, 9.2319, "MultiMedica Sesto San Giovanni"),
    "citta di sesto": (45.5342, 9.2294, "Ospedale Città di Sesto San Giovanni"),
    "sesto san giovanni": (45.5342, 9.2294, "Ospedale di Sesto San Giovanni"),
    "bassini": (45.5458, 9.2197, "Ospedale Bassini Cinisello Balsamo"),
    "san gerardo": (45.5997, 9.2731, "Fondazione IRCCS San Gerardo dei Tintori Monza"),
    "policlinico di monza": (45.5764, 9.2678, "Policlinico di Monza"),
    "policlinico monza": (45.5764, 9.2678, "Policlinico di Monza"),
    "vimercate": (45.6078, 9.3806, "Ospedale di Vimercate"),
    "desio": (45.6189, 9.2089, "Ospedale Pio XI Desio"),
    "carate": (45.6764, 9.2397, "Ospedale di Carate Brianza"),
    "giussano": (45.6989, 9.2156, "Presidio Ospedaliero di Giussano"),
    "melzo": (45.5019, 9.4217, "Ospedale Santa Maria delle Stelle Melzo"),
    "vizzolo": (45.3589, 9.3458, "Ospedale di Vizzolo Predabissi"),
    "melegnano": (45.3589, 9.3458, "Ospedale di Vizzolo Predabissi Melegnano"),
    "cernusco": (45.5244, 9.3289, "Ospedale Uboldo Cernusco sul Naviglio"),
    "gorgonzola": (45.5333, 9.4039, "Presidio Ospedaliero di Gorgonzola"),
    "bollate": (45.5489, 9.1178, "Ospedale di Bollate"),
    "ospedale di rho": (45.5317, 9.0356, "Ospedale di Circolo Rho"),
    "ospedale rho": (45.5317, 9.0356, "Ospedale di Circolo Rho"),
    "circolo rho": (45.5317, 9.0356, "Ospedale di Circolo Rho"),
    "garbagnate": (45.5719, 9.0719, "Ospedale G. Salvini Garbagnate Milanese"),
    "legnano": (45.5861, 8.9139, "Ospedale Civile di Legnano"),
    "fornaroli": (45.4644, 8.8789, "Ospedale Giuseppe Fornaroli Magenta"),
    "magenta": (45.4644, 8.8789, "Ospedale di Magenta"),
    "abbiategrasso": (45.3972, 8.9197, "Ospedale Cesare Cantù Abbiategrasso"),

    # Bergamo & Provincia
    "papa giovanni": (45.6861, 9.6458, "ASST Papa Giovanni XXIII Bergamo"),
    "gavazzeni": (45.6889, 9.6833, "Humanitas Gavazzeni Bergamo"),
    "policlinico san marco": (45.6067, 9.5989, "Policlinico San Marco Zingonia"),
    "san marco": (45.6067, 9.5989, "Policlinico San Marco Zingonia"),
    "policlinico san pietro": (45.6978, 9.5889, "Policlinico San Pietro Ponte San Pietro"),
    "san pietro": (45.6978, 9.5889, "Policlinico San Pietro Ponte San Pietro"),
    "treviglio": (45.5186, 9.6014, "Ospedale Treviglio-Caravaggio"),
    "seriate": (45.6797, 9.7189, "Ospedale Bolognini Seriate"),
    "alzano": (45.7319, 9.7319, "Ospedale Pesenti Fenaroli Alzano Lombardo"),

    # Brescia & Provincia
    "spedali civili": (45.5564, 10.2294, "ASST Spedali Civili di Brescia"),
    "civili brescia": (45.5564, 10.2294, "ASST Spedali Civili di Brescia"),
    "poliambulanza": (45.5189, 10.2289, "Fondazione Poliambulanza Brescia"),
    "citta di brescia": (45.5414, 10.1989, "Istituto Clinico Città di Brescia"),
    "san rocco": (45.6264, 10.0889, "Istituto Clinico San Rocco Ome"),
    "sant'anna brescia": (45.5189, 10.1833, "Istituto Clinico Sant'Anna Brescia"),
    "montichiari": (45.4128, 10.3889, "Ospedale di Montichiari"),
    "manerbio": (45.3564, 10.1389, "Ospedale di Manerbio"),
    "desenzano": (45.4619, 10.5369, "Ospedale di Desenzano del Garda"),
    "gavardo": (45.5869, 10.4419, "Ospedale La Memoria Gavardo"),
    "chiari": (45.5364, 9.9289, "Ospedale Mellino Mellini Chiari"),

    # Como & Lecco
    "sant'anna como": (45.7997, 9.0469, "Ospedale Sant'Anna Como - San Fermo"),
    "sant anna": (45.7997, 9.0469, "Ospedale Sant'Anna Como"),
    "valduce": (45.8089, 9.0889, "Ospedale Valduce Como"),
    "cantu": (45.7389, 9.1306, "Ospedale Antonio Abate Cantù"),
    "erba": (45.8119, 9.2278, "Ospedale Sacra Famiglia Fatebenefratelli Erba"),
    "manzoni": (45.8458, 9.4069, "Ospedale Alessandro Manzoni Lecco"),
    "mandic": (45.7006, 9.4264, "Ospedale San Leopoldo Mandic Merate"),
    "merate": (45.7006, 9.4264, "Ospedale di Merate"),

    # Varese & Provincia
    "circolo varese": (45.8117, 8.8419, "Ospedale di Circolo e Fondazione Macchi Varese"),
    "del ponte": (45.8164, 8.8358, "Ospedale Filippo Del Ponte Varese"),
    "busto arsizio": (45.6108, 8.8469, "Ospedale di Busto Arsizio"),
    "gallarate": (45.5989, 8.7906, "Ospedale Sant'Antonio Abate Gallarate"),
    "saronno": (45.6264, 9.0306, "Ospedale di Saronno"),
    "tradate": (45.7119, 8.9128, "Ospedale Galmarini Tradate"),
    "somma lombardo": (45.6819, 8.7089, "Ospedale di Somma Lombardo"),
    "luino": (45.9989, 8.7419, "Ospedale di Luino"),
    "angera": (45.7689, 8.5778, "Ospedale Carlo Ondoli Angera"),

    # Pavia & Provincia
    "policlinico san matteo": (45.2017, 9.1489, "Fondazione IRCCS Policlinico San Matteo Pavia"),
    "san matteo": (45.2017, 9.1489, "Fondazione IRCCS Policlinico San Matteo Pavia"),
    "maugeri": (45.2047, 9.1333, "ICS Maugeri Pavia"),
    "vigevano": (45.3189, 8.8689, "Ospedale Civile di Vigevano"),
    "voghera": (44.9964, 9.0119, "Ospedale Civile di Voghera"),
    "stradella": (45.0764, 9.2969, "Ospedale di Stradella"),
    "mede": (45.0964, 8.7369, "Ospedale San Martino Mede"),

    # Lodi, Cremona, Mantova, Sondrio
    "maggiore lodi": (45.3144, 9.5042, "Ospedale Maggiore di Lodi"),
    "ospedale lodi": (45.3144, 9.5042, "Ospedale Maggiore di Lodi"),
    "codogno": (45.1589, 9.7042, "Ospedale Civico di Codogno"),
    "ospedale cremona": (45.1389, 10.0469, "Ospedale di Cremona"),
    "crema": (45.3678, 9.6833, "Ospedale Maggiore di Crema"),
    "oglio po": (44.9919, 10.3958, "Ospedale Oglio Po Casalmaggiore"),
    "poma": (45.1489, 10.7819, "Ospedale Carlo Poma Mantova"),
    "ospedale mantova": (45.1489, 10.7819, "Ospedale Carlo Poma Mantova"),
    "ospedale sondrio": (46.1689, 9.8731, "Ospedale Civile di Sondrio"),
    "morbegno": (46.1364, 9.5719, "Presidio Ospedaliero di Morbegno"),
    "chiavenna": (46.3219, 9.3989, "Ospedale di Chiavenna"),
    "sondalo": (46.2719, 10.2642, "Ospedale Morelli Sondalo"),

    # Generic Fallback
    "policlinico": (45.4589, 9.1950, "Fondazione IRCCS Ca' Granda Ospedale Maggiore Policlinico"),
}

# ==============================================================================
# Known Lombardy Municipalities (Centroids for Fallback)
# ==============================================================================
KNOWN_MUNICIPALITIES: dict[str, tuple[float, float]] = {
    "milano": (45.4642, 9.1900),
    "monza": (45.5845, 9.2744),
    "bergamo": (45.6983, 9.6773),
    "brescia": (45.5416, 10.2118),
    "como": (45.8081, 9.0852),
    "varese": (45.8206, 8.8251),
    "lecco": (45.8566, 9.3977),
    "pavia": (45.1847, 9.1582),
    "lodi": (45.3139, 9.5033),
    "cremona": (45.1332, 10.0252),
    "mantova": (45.1564, 10.7914),
    "sondrio": (46.1712, 9.8728),
    "sesto san giovanni": (45.5336, 9.2306),
    "cinisello balsamo": (45.5539, 9.2144),
    "busto arsizio": (45.6119, 8.8519),
    "rho": (45.5333, 9.0400),
    "legnano": (45.5967, 8.9167),
    "gallarate": (45.5972, 8.7931),
    "vigevano": (45.3181, 8.8608),
    "cologno monzese": (45.5286, 9.2783),
    "paderno dugnano": (45.5689, 9.1689),
    "rozzano": (45.3853, 9.1539),
    "san donato milanese": (45.4189, 9.2742),
    "segrate": (45.4897, 9.2961),
    "pioltello": (45.4989, 9.3308),
    "cernusco sul naviglio": (45.5233, 9.3242),
    "treviglio": (45.5217, 9.5931),
    "crema": (45.3622, 9.6842),
    "desenzano del garda": (45.4689, 10.5369),
    "saronno": (45.6264, 9.0306),
    "voghera": (44.9964, 9.0119),
    "seriate": (45.6797, 9.7189),
    "cantù": (45.7389, 9.1306),
    "cantu": (45.7389, 9.1306),
}


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points in meters."""
    radius_earth_meters = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return radius_earth_meters * c


def parse_coordinates(val: Any) -> tuple[float, float] | None:
    """Parse coordinate input from string (lat,lon), list, or tuple."""
    if not val:
        return None

    if isinstance(val, (list, tuple)) and len(val) >= 2:
        try:
            lat = float(val[0])
            lon = float(val[1])
            if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 and not (lat == 0.0 and lon == 0.0):
                return lat, lon
        except (ValueError, TypeError):
            return None

    raw = str(val).strip()
    if not raw:
        return None

    # Handle "lat, lon", "lat; lon", or "lat lon"
    tokens = [t.strip() for t in re.split(r"[,;\s]+", raw) if t.strip()]
    if len(tokens) >= 2:
        try:
            lat = float(tokens[0])
            lon = float(tokens[1])
            if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 and not (lat == 0.0 and lon == 0.0):
                return lat, lon
        except (ValueError, TypeError):
            return None

    return None


def get_home_coordinates(criteri: dict | None = None) -> tuple[float, float] | None:
    """Retrieve user's configured home coordinates.

    Checks:
    1. criteri['home_coordinates'] or (criteri['home_latitude'], criteri['home_longitude'])
    2. os.environ['HOME_COORDINATES']
    3. os.environ['HOME_LATITUDE'] and os.environ['HOME_LONGITUDE']
    4. global settings.yaml under commute.home_coordinates

    Returns:
        (latitude, longitude) as floats, or None if not configured.
    """
    criteri = criteri or {}

    # 1. From criteri
    if "home_coordinates" in criteri and criteri["home_coordinates"]:
        coords = parse_coordinates(criteri["home_coordinates"])
        if coords:
            return coords

    if criteri.get("home_latitude") and criteri.get("home_longitude"):
        coords = parse_coordinates((criteri["home_latitude"], criteri["home_longitude"]))
        if coords:
            return coords

    # 2. From env HOME_COORDINATES
    env_coords = os.environ.get("HOME_COORDINATES", "").strip()
    if env_coords:
        coords = parse_coordinates(env_coords)
        if coords:
            return coords

    # 3. From separate env HOME_LATITUDE / HOME_LONGITUDE
    env_lat = os.environ.get("HOME_LATITUDE", "").strip()
    env_lon = os.environ.get("HOME_LONGITUDE", "").strip()
    if env_lat and env_lon:
        coords = parse_coordinates((env_lat, env_lon))
        if coords:
            return coords

    # 4. From global settings.yaml if available
    try:
        from core.config import Config
        cfg = Config.load()
        commute_cfg = (cfg.settings or {}).get("commute", {})
        if isinstance(commute_cfg, dict):
            if commute_cfg.get("home_coordinates"):
                coords = parse_coordinates(commute_cfg["home_coordinates"])
                if coords:
                    return coords
            if commute_cfg.get("home_latitude") and commute_cfg.get("home_longitude"):
                coords = parse_coordinates((commute_cfg["home_latitude"], commute_cfg["home_longitude"]))
                if coords:
                    return coords
    except Exception:
        pass

    return None


def is_commute_active(criteri: dict | None = None) -> bool:
    """Return True if home coordinates are configured and commute calculation is active."""
    return get_home_coordinates(criteri) is not None


def _load_geocache(cache_path: Path | str | None = None) -> dict[str, list[float]]:
    """Load cached geocoded coordinates from disk."""
    target = Path(cache_path or DEFAULT_GEOCACHE_PATH)
    if target.exists():
        try:
            with open(target, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


def _save_geocache(cache: dict[str, list[float]], cache_path: Path | str | None = None) -> None:
    """Save cached geocoded coordinates to disk."""
    target = Path(cache_path or DEFAULT_GEOCACHE_PATH)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        tmp.replace(target)
    except Exception as exc:
        log.debug("Could not save geocache: %s", exc)


def normalize_location_text(text: str | None) -> str:
    """Normalize location text (lowercase, alphanumeric, spaces)."""
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def geocode_facility(
    facility: str | None = None,
    municipality: str | None = None,
    province: str | None = None,
    allow_network: bool = True,
    cache_path: Path | str | None = None,
    timeout_seconds: float = 3.0,
) -> tuple[float, float] | None:
    """Geocode an appointment facility and/or municipality to (lat, lon).

    Resolution Order:
    1. Offline match in KNOWN_FACILITIES (hospital & ASST database)
    2. Local persistent geocache (data/geocache.json)
    3. Offline match in KNOWN_MUNICIPALITIES (Lombardy municipality centroids)
    4. Online OpenStreetMap Nominatim lookup (with rate-limiting & negative caching)
    """
    f_clean = (facility or "").strip()
    m_clean = (municipality or "").strip()
    p_clean = (province or "").strip()

    if not f_clean and not m_clean:
        return None

    norm_f = normalize_location_text(f_clean)
    norm_m = normalize_location_text(m_clean)

    # 1. Check local geocache
    cache_key = f"{f_clean} | {m_clean} | {p_clean}".strip().lower()
    cache = _load_geocache(cache_path)
    if cache_key in cache:
        res = cache[cache_key]
        if isinstance(res, (list, tuple)) and len(res) == 2:
            if res[0] is not None and res[1] is not None:
                try:
                    return float(res[0]), float(res[1])
                except (ValueError, TypeError):
                    pass
            else:
                return None  # Negative cached

    # 2. Match in KNOWN_FACILITIES (hospital & ASST database)
    for k in sorted(KNOWN_FACILITIES.keys(), key=len, reverse=True):
        if k in norm_f or f" {k} " in f" {norm_f} ":
            lat, lon, _ = KNOWN_FACILITIES[k]
            return lat, lon

    # 3. Match in KNOWN_MUNICIPALITIES
    if norm_m in KNOWN_MUNICIPALITIES:
        lat, lon = KNOWN_MUNICIPALITIES[norm_m]
        return lat, lon

    for mk, (m_lat, m_lon) in KNOWN_MUNICIPALITIES.items():
        if f" {mk} " in f" {norm_m} " or (norm_m and norm_m in mk):
            return m_lat, m_lon

    # 4. Online Nominatim query if allowed
    if not allow_network:
        return None

    # Construct search queries
    candidates: list[str] = []
    if f_clean and m_clean:
        candidates.append(f"{f_clean}, {m_clean}, Italia")
    if f_clean:
        candidates.append(f"{f_clean}, Lombardia, Italia")
    if m_clean:
        candidates.append(f"{m_clean}, Lombardia, Italia")

    for query in candidates:
        url = "https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + urllib.parse.quote(query)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "VisitaSubito/2.0 (+https://github.com/metagliatore/visita-subito)"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                data = json.loads(resp.read().decode())
                if data and isinstance(data, list) and len(data) > 0:
                    lat = float(data[0]["lat"])
                    lon = float(data[0]["lon"])
                    cache[cache_key] = [lat, lon]
                    _save_geocache(cache, cache_path)
                    return lat, lon
        except urllib.error.HTTPError as http_err:
            if http_err.code in (429, 403):
                log.warning("Nominatim rate-limit (%d) per '%s'. Interrompo.", http_err.code, query)
                break
            continue
        except Exception:
            continue

    # Negative caching to avoid re-querying failing addresses
    cache[cache_key] = [None, None]
    _save_geocache(cache, cache_path)
    return None


def find_nearest_transit_station(
    lat: float,
    lon: float,
    max_distance_meters: float = 15000.0,
) -> tuple[str | None, int | None]:
    """Find the nearest subway/train transit station and straight-line distance."""
    best_st = None
    min_dist = float("inf")

    for st_name, (st_lat, st_lon) in TRANSIT_STATIONS.items():
        d = haversine_distance(lat, lon, st_lat, st_lon)
        if d < min_dist:
            min_dist = d
            best_st = st_name

    if min_dist > max_distance_meters or best_st is None:
        return None, None

    return best_st, int(round(min_dist))


def calculate_commute(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    facility_name: str = "",
) -> CommuteInfo | None:
    """Calculate commute distance and travel times (car & public transit).

    Car travel time uses tiered urban/suburban/highway formulas accounting for peak traffic.
    Transit calculation models pedestrian walk to closest station, train/metro transit time,
    transfer penalties, and egress walk/shuttle to the destination facility.
    If destination is within walking distance (<= 850m), pedestrian time is emphasized.
    """
    try:
        o_lat = float(origin_lat)
        o_lon = float(origin_lon)
        d_lat = float(dest_lat)
        d_lon = float(dest_lon)
    except (ValueError, TypeError):
        return None

    if not (-90.0 <= o_lat <= 90.0 and -180.0 <= o_lon <= 180.0 and -90.0 <= d_lat <= 90.0 and -180.0 <= d_lon <= 180.0):
        return None
    if (o_lat == 0.0 and o_lon == 0.0) or (d_lat == 0.0 and d_lon == 0.0):
        return None

    straight_dist = haversine_distance(o_lat, o_lon, d_lat, d_lon)
    straight_km = straight_dist / 1000.0
    # Standard urban/suburban winding factor: 1.35
    road_km = round(straight_km * 1.35, 1)

    # 1. Car calculation (driving duration in peak traffic)
    if road_km <= 3.5:
        car_min = int(round((road_km / 19.0) * 60 + 3.0))
    elif road_km <= 10.0:
        car_min = int(round((road_km / 28.0) * 60 + 2.0))
    elif road_km <= 25.0:
        car_min = int(round((road_km / 40.0) * 60 + 3.0))
    else:
        # Extra-urban / highway / tangenziale
        car_min = int(round((12.0 / 35.0) * 60 + ((road_km - 12.0) / 75.0) * 60 + 5.0))
    car_min = max(5, car_min)
    car_text = f"🚗 {car_min} min in auto ({road_km} km)"

    # 2. Short walking distance bypass (straight distance <= 850m)
    walk_only_min = max(1, int(round((straight_dist * 1.25) / 80.0)))
    if straight_dist <= 850.0:
        dist_m = int(round(straight_dist))
        walk_text = f"🚶 {walk_only_min} min a piedi ({dist_m} m)"
        summary_text = f"{walk_text} · {car_text}"
        return CommuteInfo(
            distance_km=road_km,
            straight_km=round(straight_km, 2),
            car_minutes=car_min,
            car_text=car_text,
            transit_minutes=walk_only_min,
            transit_route="a piedi",
            transit_text=walk_text,
            walking_minutes=walk_only_min,
            is_walking=True,
            summary_text=summary_text,
            dest_coords=(d_lat, d_lon),
            origin_coords=(o_lat, o_lon),
            facility_name=facility_name,
        )

    # 3. Public transit calculation
    orig_st, w1_m = find_nearest_transit_station(o_lat, o_lon)
    if not orig_st:
        best_o = min(TRANSIT_STATIONS.keys(), key=lambda s: haversine_distance(o_lat, o_lon, *TRANSIT_STATIONS[s]))
        orig_st = best_o
        w1_m = int(haversine_distance(o_lat, o_lon, *TRANSIT_STATIONS[best_o]))

    dest_st, w2_m = find_nearest_transit_station(d_lat, d_lon)
    if not dest_st:
        best_d = min(TRANSIT_STATIONS.keys(), key=lambda s: haversine_distance(d_lat, d_lon, *TRANSIT_STATIONS[s]))
        dest_st = best_d
        w2_m = int(haversine_distance(d_lat, d_lon, *TRANSIT_STATIONS[best_d]))

    # Access duration from home to departure station
    feeder_orig = bool((w1_m or 0) > 2000)
    w1_min = max(6, int(round((w1_m or 0) / 250.0))) if feeder_orig else max(2, int(round(((w1_m or 0) * 1.25) / 80.0)))

    # Egress duration from destination station to hospital/facility
    feeder_dest = bool((w2_m or 0) > 2000)
    w2_min = max(10, int(round((w2_m or 0) / 650.0)) + 6) if feeder_dest else max(2, int(round(((w2_m or 0) * 1.25) / 80.0)))

    # Route and line identification
    l1 = [l for l in ("M1", "M2", "M3", "M4", "M5", "Metro BS", "FS") if l in (orig_st or "")] or ["FS/Metro"]
    l2 = [l for l in ("M1", "M2", "M3", "M4", "M5", "Metro BS", "FS") if l in (dest_st or "")] or ["FS/Metro"]
    common = set(l1).intersection(l2)

    st1_c = TRANSIT_STATIONS[orig_st]
    st2_c = TRANSIT_STATIONS[dest_st]
    st_dist = haversine_distance(st1_c[0], st1_c[1], st2_c[0], st2_c[1])

    if common:
        ride_min = max(3, int(round((st_dist * 1.25) / 500.0)))
        route_desc = sorted(common)[0]
    else:
        ride_min = max(6, int(round((st_dist * 1.25) / 480.0)) + 4)
        route_desc = f"{l1[0]} + {l2[0]}"

    if feeder_orig and feeder_dest:
        route_desc += " + treno/navetta"
    elif feeder_dest:
        route_desc += " + navetta/bus"
    elif feeder_orig:
        route_desc += " + bus"

    transit_min = w1_min + ride_min + w2_min
    transit_text = f"🚇 {transit_min} min con trasporto pubblico ({route_desc})"
    summary_text = f"{car_text} · {transit_text}"

    return CommuteInfo(
        distance_km=road_km,
        straight_km=round(straight_km, 2),
        car_minutes=car_min,
        car_text=car_text,
        transit_minutes=transit_min,
        transit_route=route_desc,
        transit_text=transit_text,
        walking_minutes=None,
        is_walking=False,
        summary_text=summary_text,
        dest_coords=(d_lat, d_lon),
        origin_coords=(o_lat, o_lon),
        facility_name=facility_name,
    )


def resolve_slot_commute(
    slot: Any,
    criteri: dict | None = None,
    allow_network: bool = True,
    cache_path: Path | str | None = None,
) -> CommuteInfo | None:
    """Calculate commute from user's home to the slot's appointment facility.

    If home coordinates are not configured in .env or criteri, returns None immediately.
    """
    home = get_home_coordinates(criteri)
    if not home:
        return None

    extra = getattr(slot, "extra", {}) or {}
    sede = extra.get("sede") or ""
    azienda = extra.get("azienda") or ""
    comune = extra.get("comune") or ""
    provincia = extra.get("provincia") or ""

    dest_coords = geocode_facility(
        facility=sede or azienda,
        municipality=comune,
        province=provincia,
        allow_network=allow_network,
        cache_path=cache_path,
    )

    if not dest_coords:
        return None

    facility_label = sede or azienda or comune
    return calculate_commute(
        origin_lat=home[0],
        origin_lon=home[1],
        dest_lat=dest_coords[0],
        dest_lon=dest_coords[1],
        facility_name=facility_label,
    )


def check_commute_filter(commute: CommuteInfo | None, criteri: dict | None = None) -> tuple[bool, str]:
    """Check if a slot's commute meets user distance/commute constraints.

    Predisposed for future filtering by:
    - max_distance_km: Maximum driving distance in km
    - max_car_minutes: Maximum driving duration in minutes
    - max_transit_minutes: Maximum public transit duration in minutes

    Returns:
        (True, "") if compliant or if no filters are configured.
        (False, reason_str) if non-compliant with configured limits.
    """
    if not criteri or commute is None:
        return True, ""

    max_dist = criteri.get("max_distance_km")
    if max_dist is not None:
        try:
            if commute.distance_km > float(max_dist):
                return False, f"distanza eccessiva ({commute.distance_km:.1f} km > max {max_dist} km)"
        except (ValueError, TypeError):
            pass

    max_car = criteri.get("max_car_minutes")
    if max_car is not None:
        try:
            if commute.car_minutes > int(max_car):
                return False, f"tempo auto eccessivo ({commute.car_minutes} min > max {max_car} min)"
        except (ValueError, TypeError):
            pass

    max_transit = criteri.get("max_transit_minutes")
    if max_transit is not None:
        try:
            if commute.transit_minutes > int(max_transit):
                return False, f"tempo trasporto pubblico eccessivo ({commute.transit_minutes} min > max {max_transit} min)"
        except (ValueError, TypeError):
            pass

    return True, ""


def get_commute_status_text(criteri: dict | None = None) -> str:
    """Return status description string for display in /status."""
    coords = get_home_coordinates(criteri)
    if coords:
        return f"Calcolo distanza: ATTIVO (casa: {coords[0]:.4f}, {coords[1]:.4f} - auto e trasporto pubblico)"
    return "Calcolo distanza: non configurato (imposta HOME_COORDINATES in .env)"
