"""What in a goal is about the person, and what kind of bot it asks for.

People type a goal the way they talk: "I want to build a wedding planner
bot. My brother just got engaged ...". Turned straight into search words,
that sentence would send "brother" and "engaged" to Wikipedia. This module
reads the goal on this computer and sorts its words:

* **hard** words are about the person and never leave the computer:
  relatives, pronouns, months and weekdays, numbers and ages, and the
  owner's own family when she says so ("my kids", "three kids");
* **held** words look like a name, a brand, a place or a health detail. They
  stay on the computer unless the owner ticks one (names, brands and places
  she typed), and some can never be ticked (her home town, her state, a
  compass region, health and other sensitive words, and the time of her own
  plans: a season, holiday or weekend, "next spring", "Memorial Day",
  "summer-trip", "Christmas break", which leaves only as a word of the
  bot's subject; and whatever the sentence's shape says is her private
  story, "my wife deals with X", "since my X started", "my kid with X");
* the **subject** is the kind of bot she asked for ("wedding planner",
  "minecraft server", "3d printer"). It is what every search starts from, so
  it is computed by one pinned algorithm (:func:`subject_of`) and always
  kept free of hard words, names and places.

Everything here is grammar and a spelling dictionary, never a topic table:
nothing knows what a wedding or a garden is. The dictionary is the SCOWL
en_US word list shipped in ``config/wordlists/en_US.dic`` (notice in
``config/wordlists/NOTICE.txt``); it is read once, lazily, and only tells an
ordinary word ("rose", "garden") from a proper noun ("Tucson", "Christmas").

Nothing in this module logs or stores the goal or any word of it.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import collections
import json
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Iterable, Optional

from puffpai.catalogs import goal_words

# --- grammar lists (no topics) -------------------------------------------------

#: People the owner is related to or lives among. Always hard.
RELATIVES = frozenset("""sister sisters brother brothers sibling siblings mom moms mum mums
mother mothers dad dads father fathers parent parents son sons daughter daughters wife wives
husband husbands partner partners spouse spouses fiance fiancee fiancé fiancée boyfriend
boyfriends girlfriend girlfriends nephew nephews niece nieces aunt aunts uncle uncles cousin
cousins grandma grandmas grandpa grandpas grandmother grandmothers grandfather grandfathers
grandson grandsons granddaughter granddaughters grandkid grandkids grandchild grandchildren
stepson stepdaughter stepmom stepdad stepmother stepfather inlaws in-laws bride groom
friend friends neighbor neighbors neighbour neighbours roommate roommates coworker coworkers
boss hubby hubbies wifey sis bro bros mama mamas papa papas nana nanas nanna granny grannies
gran gramps grampa grandad granddad grandads auntie aunties godson godsons goddaughter
goddaughters godmother godmothers godfather godfathers godparent godparents godchild
godchildren stepsister stepsisters stepbrother stepbrothers stepsons stepdaughters stepkid
stepkids stepchild stepchildren stepmoms stepdads bestie besties bff bffs ex exes""".split())

#: Words that say who a bot is for. Allowed out on their own ("a quiz for
#: kids"); hard when the goal makes them her own ("my kids", "three kids").
AUDIENCE = frozenset("""kid kids child children family families teen teens teenager
teenagers baby babies toddler toddlers""".split())

#: People and animals that have a name of their own when they are hers ("my
#: little girl Rose", "our puppy Biscuit"). Not personal on their own (a
#: "dog training bot" searches "dog"); they only mark the word after them.
BEINGS = frozenset("""girl girls boy boys kiddo kiddos twin twins guy guys lady ladies
dog dogs doggo puppy puppies pup pups cat cats kitten kittens kitty kitties pet pets horse
horses pony ponies bunny bunnies rabbit rabbits hamster hamsters parrot parrots bird birds
student students client clients""".split())

#: Joining and function words. Never a name, even capitalised ("A Chess Bot
#: For My Son And Daughter").
FUNCTION_WORDS = frozenset("""a an the and or nor but of for to with in on at by from as is
are was were be been am do does did so if then than that this these those it its into onto
up out off over under about via per who whose which what when where""".split())

#: "her name is Grace", "whose name is Grace", "his name's Leo".
NAME_NOUNS = frozenset("name names nickname nicknames".split())

PRONOUNS = frozenset("""i i'm im i'll i've i'd me my mine myself we we're we'll we've us our
ours ourselves you your yours yourself he him his himself she her hers herself they them
their theirs themselves it's""".split())

#: Words that make the next word(s) the owner's own: "my kids", "our dog".
POSSESSIVES = frozenset("my our his her their your".split())

#: Words people use to tell their news ("we just moved", "my brother just
#: got engaged", "we are opening a bakery"). These are ordinary words, so they
#: are NOT hard and never shown as "Kept private" ("learn the openings",
#: "a retirement planner bot" must keep their words). They are left out of
#: the goal's search words only (``goal_words.CONTEXT_STOPWORDS``). The
#: sensitive ones moved to :data:`SENSITIVE_WORDS` (divorce: legal; died,
#: widowed: grief; pregnant: health).
LIFE_EVENTS = frozenset("""married marry marrying engaged engagement moved moving adopted
adopting retired retiring graduated graduating born opening getting expecting
separated""".split())

#: Sensitive subjects: a word from
#: one of these is held from every search phrase unless it IS the bot's
#: subject ("a sobriety tracker bot"), and then the subject carries a plain
#: note. Never allowed out by a tick. Grammar and meaning categories, not
#: topics of any one goal. ``health`` covers mental health, addiction and
#: sexual health; everything else is ``private`` on the owner's screen.
_SENSITIVE_SOURCE = {
    "health": """anxiety anxious panic depressed depressive trauma traumatic therapy therapies
therapist counseling counselling counselor counsellor psychologist psychiatric psychiatry
psychotherapy insomnia sobriety sober relapse relapsed addict addicts addicted alcoholism
alcoholic alcoholics detox opioid opioids opiate opiates methadone suboxone buprenorphine
naloxone narcan heroin fentanyl cocaine meth methamphetamine herpes aids std stds sti stis
chlamydia gonorrhea syphilis hpv abortion abortions contraception contraceptive
contraceptives fertility infertile vasectomy erectile viagra menopause obesity obese
pregnant pregnancy miscarriage dysphoria hiv cancer chemo chemotherapy radiotherapy
oncology oncologist tumor tumors tumour tumours dialysis ivf suicide suicidal bipolar
schizophrenia schizophrenic ptsd psychosis psychotic dementia rehab
alcohol alcohols drinking drunk drunkenness booze hangover hangovers liquor
drug drugs marijuana cannabis thc cbd ecstasy mdma lsd psychedelic psychedelics
psilocybin ketamine overdose overdosed overdoses nicotine vape vapes vaping cigarette
cigarettes gambling gamble gambler gamblers gambled selfharm stillbirth stillbirths
stillborn bpd aa xanax alprazolam adderall ritalin methylphenidate vyvanse concerta klonopin
clonazepam valium diazepam ativan lorazepam ambien zolpidem prozac fluoxetine zoloft
sertraline lexapro escitalopram paxil paroxetine celexa citalopram wellbutrin bupropion
effexor venlafaxine cymbalta duloxetine seroquel quetiapine abilify aripiprazole risperdal
risperidone lamictal lamotrigine depakote gabapentin lyrica pregabalin oxycodone oxycontin
hydrocodone vicodin percocet codeine morphine tramadol dilaudid naltrexone antabuse
chantix ozempic wegovy semaglutide metformin antidepressant antidepressants
antipsychotic antipsychotics benzodiazepine benzodiazepines benzo benzos stimulant
stimulants testosterone estrogen estradiol progesterone hrt hormone hormones condom
condoms""",
    "sex": """sex sexual sexuality sexually gay gays lesbian lesbians bisexual queer lgbt lgbtq
lgbtqia transgender trans nonbinary intersex asexual pansexual homosexual heterosexual
genderqueer genderfluid porn pornography erotic erotica kink kinky fetish hookup hookups
orgasm libido grindr homophobia homophobic transphobia transphobic biphobia dating sexting
nudes onlyfans escort escorts closeted affair affairs""",
    "belief": """religion religions religious faith church churches chapel parish
congregation clergy pastor priest rabbi imam ministry mosque mosques synagogue synagogues
bible bibles biblical quran koran torah talmud scripture scriptures prayer prayers pray
praying worship sermon sermons christian christians christianity catholic catholics
protestant baptist methodist lutheran mormon evangelical muslim muslims islam islamic
jewish jew jews judaism hindu hindus hinduism buddhist buddhists buddhism sikh sikhism
atheist atheists atheism agnostic pagan wicca wiccan spiritual spirituality god jesus
allah ramadan passover hanukkah baptism rosary hijab mitzvah jehovah jehovahs""",
    "legal": """custody parole probation eviction evicted evict immigration immigrant
immigrants undocumented deportation deported deport asylum refugee refugees naturalization
arrest arrested jail jailed prison prisoner incarcerated incarceration inmate lawsuit
lawsuits sued suing lawyer lawyers attorney attorneys felony felon misdemeanor conviction
convicted criminal dui dwi divorce divorced divorcing alimony bail subpoena indictment
indicted expungement citizenship sentenced sentencing cps visa visas convict convicts""",
    "money": """bankruptcy bankrupt debt debts indebted debtor salary salaries paycheck
paychecks wage wages income incomes foreclosure foreclosed repossession repossessed
unemployed unemployment jobless layoff layoffs welfare poverty homeless homelessness
payday overdraft overdrawn garnishment garnishments insolvency insolvent overdue arrears
delinquent""",
    "violence": """abuse abused abuser abusers abusive abusing rape raped rapist assault
assaulted violence violent stalking stalker stalked harassment harassed trafficking
trafficked molest molested molestation battered bullying bullied beaten bruise bruises
bruised strangled strangle""",
    "grief": """grief grieving grieve bereavement bereaved mourning mourn funeral funerals
hospice palliative dying died death deaths deceased widow widows widowed widower obituary
obituaries condolence condolences burial cremation cremate cremated cremating euthanasia memorial memorials grave
graves graveside gravesite cemetery cemeteries gravestone gravestones headstone headstones
casket caskets coffin coffins mortuary""",
}
#: Wider word families per category ("mastectomy", "incest",
#: "transitioning", "medicaid", "sabbath", "polyamorous", "murdered" and more
#: from the same categories).
#: Words with a common everyday meaning ("transplant" seedlings, "bishop" in
#: chess, "shooter" games, "naked" cake) are left out or only listed as a
#: pair in :data:`SENSITIVE_PHRASES`.
_SENSITIVE_MORE = {
    "health": """mastectomy mastectomies lumpectomy hysterectomy biopsy biopsies prognosis
prognoses remission metastatic metastasis metastasized malignant malignancy terminally
hospitalized hospitalised hospitalization hospitalisation miscarry miscarried miscarries
miscarrying miscarriages amputee amputees amputation amputations amputated paralyzed
paralysed paralysis paraplegic quadriplegic concussion concussions mammogram mammograms
ultrasound ultrasounds mri mris icu nicu covid coronavirus disabled disability
disabilities handicapped prosthetic prosthesis illness illnesses chronic diagnosed
gynecologist gynaecologist obgyn urologist proctologist psychiatrist psychiatrists
neurologist cardiologist dermatologist ocd anorexia anorexic bulimia bulimic mania manic
tourette tourettes ssri ssris hungover narcissist narcissists narcissistic sids
colostomy ileostomy catheter incontinence incontinent""",
    "sex": """incest incestuous pedophile pedophiles pedophilia paedophile paedophiles
paedophilia pedo prostitution prostitute prostitutes polyamory polyamorous polyam swinger
swingers dyke dykes fag fags faggot faggots tranny trannies transsexual transsexuals
transitioning transitioned detransition detransitioned detransitioning crossdresser
crossdressers crossdressing infidelity adultery adulterous mistress mistresses brothel
brothels masturbation masturbate impotence impotent bdsm virginity
celibate celibacy incel incels sextortion threesome orgy polygamy polygamous""",
    "belief": """sabbath sabbaths shabbat shabbos communion communions eucharist kabbalah
satanism satanist satanists satanic occult witchcraft episcopal episcopalian
episcopalians orthodox presbyterian presbyterians pentecostal adventist adventists quaker
quakers amish mennonite mennonites unitarian anglican anglicans calvinist scientology
scientologist scientologists rastafarian zoroastrian jain jainism shinto taoism taoist
bahai baptized baptised christening deacon deacons nun nuns monk monks convent monastery
pope papal vatican missionary missionaries apostle apostles tithe tithing rapture
catechism seminary yeshiva madrasa gurdwara mandir sacrament sacraments confession
confessions eid hajj umrah psalm psalms gospel hymn hymns prophet messiah salvation
sinner sinners atonement resurrection crucifix crucifixion priesthood mormonism lds
reincarnation""",
    "legal": """defendant defendants plaintiff prosecutor prosecutors prosecution prosecuted
parolee parolees burglary burglarized robbery robbed shoplifting shoplifted fraud
fraudulent scammed extortion blackmail blackmailed guardianship conservatorship
visitation illegals daca h1b greencard detained detainee detainees expunged
acquitted arraignment plea pleaded restitution mugshot lien liens""",
    "money": """medicaid medicare ebt foodbank ssi ssdi hardship furlough furloughed pawnshop
pawned bankrupted""",
    "violence": """murder murders murdered murderer murderers murdering homicide homicides
manslaughter kidnap kidnaps kidnapped kidnapping kidnapper abduct abducted abduction
abductor hostage hostages stabbed stabbing stabbings gunshot gunshots torture tortured
torturing mugged mugging carjacked carjacking terrorism terrorist
terrorists victim victims victimized bully bullies coercion coercive gaslighting gaslit
punched slapped""",
    "grief": """orphan orphans orphaned orphanage euthanize euthanized euthanise euthanised
euthanizing euthanising eulogy eulogies autopsy autopsies coroner embalming mourners""",
}
#: More of the same categories ("gastric bypass", "liposuction",
#: "prostate", "lice", "juvie", "WIC"):
#: operations and body health, reproductive health, legal trouble, money
#: hardship. Words with an everyday meaning too are in :data:`AMBIGUOUS`.
_SENSITIVE_FX = {
    "health": """gastric bariatric liposuction lipo facelift facelifts prostate prostatic
hemorrhoid hemorrhoids haemorrhoid haemorrhoids bedwetting enuresis lice scabies
semen surrogacy cesarean caesarean csection hernia hernias
circumcision laxative laxatives enema enemas suppository suppositories surgery surgeries
ostomy stoma dispensary botox""",
    "legal": """juvie adoption adoptions""",
    "money": """wic loans""",
    "sex": """cheated""",
    "violence": """groomed""",
}
#: More words ("psoriasis", "thyroid", "kratom",
#: "vibrator", "probate", "cardiology", "hallucinations" and more).
#: Conditions, medicines, body and sexual health words, specialties, legal
#: and money trouble. Everyday meanings are in :data:`AMBIGUOUS` or
#: :data:`_ORDINARY_BEFORE`. The grammar rules in :func:`_story_holds` hold
#: what no list names.
_SENSITIVE_FX2 = {
    "health": """psoriasis eczema rosacea vaginismus vulvodynia endometriosis pcos crohns crohn
colitis ibs ibd gerd copd ckd uti utis thyroid hypothyroidism hyperthyroidism hashimotos
gout gallstones gallbladder narcolepsy huntingtons sclerosis osteoporosis glaucoma
cataract cataracts tinnitus stutter stuttering stammer stammering palsy bifida fibrosis
hemophilia haemophilia hemophiliac pacemaker stent stents ventilator cpap bipap epipen
kratom mounjaro zepbound phentermine clozapine haldol vivitrol sublocade subutex campral
acamprosate disulfiram shrooms mescaline peyote dmt ayahuasca ghb rohypnol roofies junkie
junkies inpatient outpatient paranoia paranoid hallucination hallucinations hallucinate
hallucinating dissociative dissociation depersonalization derealization suicidality
alanon narcotic narcotics cardiology cardiologist neurology dermatology gastroenterology gastroenterologist
endocrinology endocrinologist rheumatology rheumatologist pulmonology pulmonologist
nephrology nephrologist urology gynecology gynaecology hematology haematology hematologist
obstetrics obstetrician podiatrist orthopedist oncologists radiologist chemotherapies
anemia anaemia anemic anaemic dyspraxia dyscalculia apraxia aphasia vertigo apnea cochlear
tbi sepsis menstruation
menstrual pms pmdd vaginal vagina penis penile testicular ovarian uterine erection
erections ejaculation ejaculate pregnancies prenatal postpartum lactation
colonoscopies""",
    "sex": """vibrator vibrators dildo dildos lube sextoy sextoys reassignment
genderfluid""",
    "legal": """probate bondsman vandalism trespassing trespassed overstay overstayed deportee
deportees asylee asylees felonies""",
    "money": """tanf""",
    "violence": """cyberbullying cyberbully cyberbullied cyberstalking cyberstalker catfished
catfishing""",
    "grief": """widowhood""",
    "belief": """kippur""",
}
#: Words such as "sciatica",
#: "perimenopause", a genital skin condition, "ectopic", "xylazine", "Truvada",
#: "hymen", "seance", "hearse", "dv shelter", "paternity test", for
#: the "for my X" and "my daughter's X" shapes, where grammar cannot tell a
#: condition from a garden). Conditions, body words, medicines, belief,
#: legal and grief words. Everyday meanings too ("thrush" the bird, "tarot"
#: the card game, "restraining" a dog) are in :data:`AMBIGUOUS`.
_SENSITIVE_FX3 = {
    "health": """sciatica perimenopause perimenopausal menopausal postmenopausal premenopausal
ectopic genital genitals genitalia wart warts ringworm hymen xylazine truvada descovy
biktarvy apretude cabenuva cgm syndrome syndromes lupus celiac coeliac
fibromyalgia sciatic hemorrhoidectomy incontinent herniated""",
    "belief": """tarot seance seances bris ouija""",
    "legal": """paternity restraining""",
    "grief": """hearse hearses""",
    "violence": """dv ipv""",
}
#: Words such as
#: "iud", "prenup", "annulment", "fibroids", "heart bypass", "warrant",
#: "trial separation", "court-ordered anger management". The
#: grammar in :func:`_story_holds` holds such words in a reason clause about
#: her or her people whatever they are; these are for the other shapes ("our
#: annulment paperwork", "my IUD check-ups").
_SENSITIVE_FINAL = {
    "health": """iud iuds fibroid fibroids ecg ekg eeg delusion delusions delusional
bypass""",
    "legal": """prenup prenups prenuptial annulment annulments annul annulled annulling
warrant warrants separation courtordered""",
    "sex": """stripclub stripclubs""",
}
#: Words such as "clomid", "ovulation", "asperger", "pinworms", "dnr", "ppd",
#: "republican", "cartel", "hazing". Conditions, medicines by
#: their common and brand names, fertility care, identity, political belief,
#: legal and money trouble. Listed words lose to an earlier list's category.
#: Words with an everyday meaning too are in :data:`PERSONAL_SENSITIVE`.
_SENSITIVE_FX5 = {
    "health": """acne bunion bunions pinworm pinworms asperger aspergers ovulation ovulating
ovulate clomid letrozole femara follistim menopur ovidrel iui ppd dnr polst macular
keratoconus arrhythmia afib tachycardia bradycardia angina hypertension hypotension
triglycerides prediabetes prediabetic gastroparesis jaundice fistula prolapse myasthenia
ataxia dystonia cataplexy insomniac schizoaffective cptsd arfid trichotillomania
dermatillomania agoraphobic hypomania mrsa lyme impetigo candida vitiligo alopecia keloid
endometrial preeclampsia eclampsia epidural gyno cialis levitra lisinopril lipitor
levothyroxine synthroid amlodipine prilosec neurontin hydrochlorothiazide remeron buspirone
buspar zyprexa divalproex topiramate topamax levetiracetam keppra carbamazepine tegretol
oxcarbazepine lisdexamfetamine strattera guanfacine intuniv modafinil provigil prednisone
prednisolone methotrexate humira enbrel remicade stelara dupixent tirzepatide trulicity
jardiance farxiga januvia lantus humalog novolog victoza saxenda contrave qsymia warfarin
coumadin eliquis apixaban xarelto rivaroxaban plavix clopidogrel furosemide lasix
spironolactone tamoxifen anastrozole arimidex herceptin keytruda opdivo norco accutane
isotretinoin finasteride propecia minoxidil valtrex acyclovir flagyl metronidazole diflucan
fluconazole nebulizer dexcom omnipod juul doula strep tics nightterrors bedbugs disorder
disorders disease diseases deficiency deficiencies murmur embolism thrombosis toxicity mutism
dysmorphia dysmorphic hyperemesis addisons cushings sjogrens danlos ibogaine spravato percs
xanny deadname deadnamed polycule""",
    "sex": """aromantic cisgender mtf ftm camgirl camgirls""",
    "belief": """puja shiva republican republicans democrat democrats gop maga libertarian
libertarians socialist socialists socialism communist communists communism marxist marxists
antifa blm nra leftist leftists qanon prolife prochoice exorcism exorcisms excommunicated
excommunication shunning shunned astrology""",
    "legal": """duis expunge contraband cartel cartels smuggling""",
    "money": """sportsbook sportsbooks draftkings fanduel""",
    "violence": """hazing hazed""",
    "grief": """morgue""",
}
#: Short words of this list never count as half of a glued word ("gop" +
#: "her" is not "gopher"): too many made-up words start or end with them.
_NO_GLUE = frozenset(w for words in _SENSITIVE_FX5.values() for w in words.split()
                     if len(w) <= 3)

#: Words that are private only when the goal is about her or her people, and
#: then only on their own (they never make the whole goal private): each has
#: an everyday meaning too ("asphalt shingles", "a lithium battery", "sheet
#: pan", "dog grooming", "the books I lent"). Next to one of the words
#: listed with it (before or after), it is that everyday thing.
PERSONAL_SENSITIVE: dict[str, tuple[str, frozenset[str]]] = {
    w: (cat, frozenset(near.split())) for cat, rows in {  # "<x": x before it only; ">x": after
        "health": {
            "shingles": "roof roofs roofing asphalt cedar wood wooden slate metal vinyl new "
                        "replace replacing replacement shingle",
            "lithium": "battery batteries ion cell cells polymer iron",
            "crack": "code codes egg eggs safe window windows windshield sidewalk wall walls "
                     "phone screen screens joke jokes up open case puzzle puzzles cipher "
                     "ciphers password passwords foundation driveway concrete ceiling "
                     ">the >down <to",
            "ed": "driver drivers driving",
            "ms": "word excel office teams paint dos sql access outlook project",
            "als": "",
            "nightmare": "mode difficulty level before christmas",
            "nightmares": "mode difficulty level",
            "puberty": "",
            "blockers": "ad ads pop popup popups sun draft tracker trackers spam cookie "
                        "cookies script scripts",
            "pills": "",
            "tobacco": "pipe pipes farm farms farming farmer farmers barn history leaf",
            "edibles": "wild foraging forage foraged garden plants plant mushroom mushrooms",
            "blackout": "curtain curtains shades shade blinds blind dates date period power "
                        "lining poetry",
            "withdrawal": "bank cash atm money account accounts troop troops early 401k ira "
                          "fee fees savings",
            "cyst": "", "cysts": "",
            "tremors": "earthquake earthquakes seismic quake quakes",
            "deaf": "",
            "embryo": "chicken chick egg eggs frog fish development biology science",
            "midwife": "",
            "degeneration": "",
            "terrors": "",
            # In a story ("after his stroke", "who has been cutting", "on the
            # spectrum") the word is held.
            "stroke": "swim swimming swimmer swimmers back backstroke breast butterfly "
                      "freestyle brush brushes paint painting painter pen pencil "
                      "calligraphy kanji hanzi character characters letter letters order "
                      "golf tennis rowing row paddle paddling kayak kayaking piston engine "
                      "cylinder two four genius luck key >of >order >count >rate",
            "strokes": "swim swimming swimmer swimmers back breast butterfly freestyle "
                       "brush brushes paint painting painter pen pencil calligraphy kanji "
                       "hanzi character characters letter letters order golf tennis rowing "
                       "paddle paddling kayak key >of >order >count",
            "cutting": "paper board boards edge edges wood hair grass lawn hedge hedges "
                       "coupon coupons cost costs carbs carb calories sugar machine machines "
                       "cricut vinyl fabric quilt quilting sewing pattern patterns garden "
                       "flower flowers stem stems plant plants propagation tile tiles glass "
                       "metal laser die cookie cookies tool tools onion onions vegetable "
                       "vegetables veggies meat knife knives stencil stencils sticker "
                       "stickers mat mats tree trees branch branches firewood lumber diamond "
                       "gem gems gemstone gemstones stone cake bread cord cable class "
                       "classes >back >down >out >up",
            "spectrum": "color colors colour colours light lights visible electromagnetic "
                        "analyzer analyser analysis internet cable wifi isp tv router modem "
                        "bill bills outage outages service zx emission absorption radio "
                        "frequency frequencies rainbow political wide full broad mobile "
                        "channel channels news band bands sound audio solar mass infrared "
                        "uv",
        },
        "sex": {
            # In a story ("my wife caught me cheating") "cheating" is held.
            "cheating": "test tests exam exams quiz quizzes homework game games card cards "
                        "chess poker monopoly code codes detection detector detect anti "
                        "school class essay essays golf diet meal meals day days death "
                        "online academic proctor proctoring >at",
            "binder": "ring recipe recipes school notebook notebooks pokemon card cards "
                      "trading coin coins stamp stamps clip clips three",
            "drag": "and drop dropping race races racing strip car cars coefficient lift "
                    "click mouse <to >the >my >files >it",
            "pronouns": "grammar english spanish french german learn learning lesson "
                        "lessons personal possessive relative subject object demonstrative",
            "bi": "weekly monthly annual annually yearly",
            "pan": "sheet frying cake pizza baking bake cast iron roasting saute saucepan "
                   "tilt zoom camera cameras pans muffin loaf skillet",
            "stripper": "paint wire wires varnish floor furniture",
            "strippers": "paint wire wires varnish floor furniture",
        },
        "belief": {
            "lent": ">out >to >me >him >her >them >us <books <book <money <i <we <you <he "
                    "<she <they <was <were",
            "liberal": "arts art use amount amounts sprinkle",
            "conservative": "estimate estimates guess guesses approach investing investment "
                            "investments portfolio",
            "progressive": "web app apps jackpot rock overload lens lenses resistance "
                           "difficulty loading enhancement download",
        },
        "legal": {
            "gang": "chain gangs four",
            "gangs": "chain",
            "dealer": "card cards car cars auto blackjack antique antiques art coin coins "
                      "stamp stamps comic comics book books used poker",
            "dealers": "card cards car cars auto antique antiques art coin coins stamp "
                       "stamps comic comics book books used",
            "commissary": "military base navy army",
        },
        "money": {
            "lottery": "",
            "betting": "",
            "casino": "game games night royale party theme",
            "inheritance": "class classes python java object objects oop code multiple "
                           "javascript prototype css",
            "pawn": "chess structure structures move moves promotion storm endgame endgames "
                    "opening openings",
        },
        "violence": {
            "grooming": "dog dogs pet pets cat cats horse horses beard hair salon kit tools "
                        "table business shop",
            "runaway": "train trains bride hit success truck ramp jury",
            "runaways": "",
        },
        "grief": {
            "ashes": "wood fireplace fire volcanic volcano pokemon",
            "wake": "up word words time times alarm alarms boat boarding surf surfing "
                    "board boards >me >my >us >him >her >them >the >everyone >everybody "
                    "<to <will <can <i <we <you",
            "urn": "coffee tea",
        },
    }.items() for w, near in rows.items()}


#: Things of a home: appliances, furniture, rooms and buildings, vehicles,
#: devices, mobility aids. One that one of her people owns ("my mom's
#: dryer") is held until she ticks it.
HOUSEHOLD_NOUNS = frozenset("""dryer dryers washer washers fridge fridges refrigerator freezer
freezers stove stoves oven ovens microwave microwaves dishwasher dishwashers toaster blender
kettle mixer furnace heater heaters boiler thermostat ac fan fans sink faucet toilet shower
tub bathtub door doors window windows fence gate deck porch patio garage shed barn roof
gutter gutters couch sofa chair chairs recliner table tables desk bed beds crib mattress
dresser cabinet cabinets shelf shelves closet lamp lamps mirror rug carpet basement attic
kitchen bathroom bedroom den yard driveway pool hottub car cars truck trucks van minivan suv
bike bikes scooter motorcycle boat rv trailer camper walker wheelchair cane scooter hearing
aid aids glasses phone phones laptop laptops computer computers tablet tablets tv printer
router camera cameras doorbell lock locks vacuum mower""".split())


#: Words that make a goal about her own life or her people's.
_PERSONAL_MARKS = frozenset("""my our ours mine his her hers their theirs we us he she him
them they""".split())


def _goal_is_personal(raw: list[str]) -> bool:
    """Does the goal speak about her life or her people ("my", "our", "he",
    "my mom")? A goal that only says "I want" ("I want a pan pizza recipe
    bot", "I'd like help with the grooming too") is about a thing."""
    return any(w in _PERSONAL_MARKS or _kin(w) for w in raw)


def personal_sensitive_at(raw: list[str], k: int) -> str:
    """The category of the word at *k* of the goal's words when it is one of
    :data:`PERSONAL_SENSITIVE` and no word listed with it is next to it,
    ``""`` otherwise. The caller checks that the goal is personal."""
    w = raw[k]
    got = PERSONAL_SENSITIVE.get(w)
    if got is None:
        return ""
    cat, near = got
    # Past a possessive "s": "driver's ed" is the driver's.
    p = k - 1
    if p >= 1 and raw[p] == "s":
        p -= 1
    prev = raw[p] if p >= 0 else ""
    nxt = raw[k + 1] if k + 1 < len(raw) else ""
    if "<" + prev in near or ">" + nxt in near:
        return ""
    # A plain neighbour word counts up to two words away ("inheritance and
    # classes", "the books I lent"); a "<"/">" one only right next to it.
    around = raw[max(0, p - 1):p + 1] + raw[k + 1:k + 3]
    if any(x in near for x in around):
        return ""
    return cat


#: What ends a clause for the neighbour rule: punctuation and line breaks.
_CLAUSE_SPLIT_RE = re.compile(r"[,.;:!?()\[\]/\"“”\n\r]+")
#: Verbs that tie a word to the person in front of them: "my mom had a
#: stroke", "she was cutting", "my son is on the spectrum", "my wife keeps
#: cheating", "she's cutting" (the "s" of "she's").
_KIN_STORY_VERBS = frozenset("""am is are was were be been being has have had having keeps
kept keep keeping started starts start began begun begins suffered suffers suffering got gets
getting caught catches s re m ve""".split())
#: Words that may sit between that verb and the word: "had a mild stroke",
#: "is somewhere on the spectrum", "has been cutting again".
_KIN_STORY_GAP = frozenset("""a an the on in of some mild minor small major massive bad severe
second another third somewhere kind sort""".split())
#: People (besides relatives) who can be the one in such a story.
_STORY_PERSONS = frozenset("i we he she they who you".split())
_HUMAN_BEINGS = frozenset("""girl girls boy boys kiddo kiddos twin twins guy guys lady ladies
student students client clients""".split())


def _clause_words(text: str) -> list[list[str]]:
    """The goal's words, one list per clause (split at punctuation and line
    breaks)."""
    return [ws for part in _CLAUSE_SPLIT_RE.split(text)
            if (ws := goal_words.raw_words(part))]


def _kin_story_at(raw: list[str], k: int) -> bool:
    """Is the word at *k* of a clause's words what a person is going
    through: a person, a "be"/"have" verb (or "keeps", "started", "got"),
    then only articles, "on"/"in", a few size words or words of time, then
    the word ("my mom had a mild stroke", "she was cutting with paper clips",
    "my son is on the spectrum", "whether my wife is cheating online")? Or
    the word right after a possessive ("her cutting", "his mild stroke")?

    In a story the word is never released by a neighbour, even one with the
    everyday meaning."""
    j = k - 1
    verb = False
    while j >= 0 and (raw[j] in _KIN_STORY_GAP or raw[j] in _KIN_STORY_VERBS
                      or raw[j] in _HAVE_ADVERBS or raw[j] in _TIME_ADVERBS):
        verb = verb or raw[j] in _KIN_STORY_VERBS
        j -= 1
    if j < 0:
        return False
    w = raw[j]
    if not verb:
        return w in POSSESSIVES
    if w in _STORY_PERSONS or _kin(w):
        return True
    return bool(_forms(w) & (AUDIENCE | _HUMAN_BEINGS)) and j > 0 and raw[j - 1] in POSSESSIVES


#: Who can be the one in her people's news (never "I"/"we": "we just moved"
#: and "I'm opening a bakery" are her own plain news).
_NEWS_PERSONS = frozenset("he she they".split())
#: Which baby it is, in her people's news ("having her first baby").
_BIRTH_ORDER = frozenset("first second third fourth fifth 1st 2nd 3rd 4th 5th".split())


def _their_news_at(raw: list[str], k: int) -> int:
    """Is the news word at *k* ("married", "engaged", "retiring") what one
    of her people is doing: "my sister is engaged", "my brother just
    got engaged", "my mom who is retiring", "she's moving", "after the twins
    were born"? Without this, "married" is not sent but not listed as kept
    private either.

    Returns the index of the person (the "twins" of "the twins were born" is
    held with the news), or -1 when it is not her people's news."""
    j = k - 1
    while j >= 0 and (raw[j] in _KIN_STORY_VERBS or raw[j] in _HAVE_ADVERBS
                      or raw[j] in _TIME_ADVERBS or raw[j] in LIFE_EVENTS
                      or raw[j] in ("got", "gets", "getting", "about", "to")):
        j -= 1
    if j < 0 or j == k - 1:
        return -1
    w = raw[j]
    if w == "who" and j > 0:
        j -= 1
        w = raw[j]
    if w in _NEWS_PERSONS or _kin(w):
        return j
    if _forms(w) & (AUDIENCE | _HUMAN_BEINGS) and j > 0 \
            and (raw[j - 1] in POSSESSIVES or raw[j - 1] == "the"):
        return j
    return -1


def personal_sensitive_words(text: str) -> list[tuple[str, str]]:
    """``[(word, category)]`` for each :data:`PERSONAL_SENSITIVE` word of a
    goal about her or her people that is private where it sits.

    A word listed with it releases it only inside its own clause ("swim
    stroke", "paper cutting"), never from another clause ("my mom had a mild
    stroke, a painting bot") and never inside a story about a person (see
    :func:`_kin_story_at`). The caller checks that the
    goal is personal."""
    out: list[tuple[str, str]] = []
    for raw in _clause_words(text):
        for k, w in enumerate(raw):
            got = PERSONAL_SENSITIVE.get(w)
            if got is None:
                continue
            cat = personal_sensitive_at(raw, k) or (got[0] if _kin_story_at(raw, k) else "")
            if cat:
                out.append((w, cat))
    return out


#: Long-term conditions the data classifier's list misses in some form
#: ("epileptic", "dyslexia", "alzheimer" without its "'s"). Held as health
#: words like the classifier's own, so, like them, they do not make the whole
#: goal private ("a meal planner bot, my son is diabetic" still plans meals).
HEALTH_HELD_WORDS = frozenset("""epilepsy epileptic autism autistic adhd diabetes diabetic
insulin dementia alzheimer alzheimers parkinson parkinsons dyslexia dyslexic arthritis
arthritic asthma asthmatic migraine migraines""".split())
SENSITIVE_WORDS: dict[str, str] = {
    w: cat for src in (_SENSITIVE_FX5, _SENSITIVE_SOURCE, _SENSITIVE_MORE, _SENSITIVE_FX,
                       _SENSITIVE_FX2, _SENSITIVE_FX3, _SENSITIVE_FINAL)
    for cat, words in src.items() for w in words.split()}
#: Sensitive only as a pair: "passed away", "mental health", "coming out".
SENSITIVE_PHRASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("passed", "away"), "grief"), (("end", "of", "life"), "grief"),
    (("mental", "health"), "health"), (("mental", "illness"), "health"),
    (("eating", "disorder"), "health"), (("birth", "control"), "health"),
    (("coming", "out"), "sex"), (("gender", "identity"), "sex"),
    (("sex", "life"), "sex"), (("green", "card"), "legal"),
    (("restraining", "order"), "legal"), (("food", "stamps"), "money"),
    (("laid", "off"), "money"), (("domestic", "violence"), "violence"),
    (("sexual", "orientation"), "sex"), (("criminal", "record"), "legal"),
    (("self", "harm"), "health"), (("self", "harming"), "health"),
    (("self", "injury"), "health"), (("court", "date"), "legal"),
    (("court", "case"), "legal"), (("court", "hearing"), "legal"),
    (("court", "order"), "legal"), (("family", "court"), "legal"),
    (("debt", "collector"), "money"), (("child", "support"), "legal"),
    # Pairs from real sentences in the sensitive categories.
    (("loss", "of"), "grief"), (("bad", "credit"), "money"),
    (("credit", "repair"), "money"), (("collection", "agency"), "money"),
    (("collection", "call"), "money"), (("student", "loan"), "money"),
    (("tinder", "date"), "sex"),
    # "log my AA meetings", "my gender transition".
    (("aa", "meeting"), "health"), (("aa", "meetings"), "health"),
    (("na", "meeting"), "health"), (("na", "meetings"), "health"),
    (("alcoholics", "anonymous"), "health"), (("narcotics", "anonymous"), "health"),
    (("gender", "transition"), "sex"), (("medical", "transition"), "sex"),
    # Someone hurting her, said with everyday verbs ("my husband hits me").
    (("hits", "me"), "violence"), (("hit", "me"), "violence"),
    (("beats", "me"), "violence"), (("beat", "me"), "violence"),
    (("hurts", "me"), "violence"), (("choked", "me"), "violence"),
    (("chokes", "me"), "violence"), (("threatens", "me"), "violence"),
    (("cheating", "on"), "sex"), (("cheated", "on"), "sex"),
    # "public defender", "credit score", "food bank": pairs whose words are
    # ordinary on their own.
    (("public", "defender"), "legal"), (("sex", "offender"), "legal"),
    (("identity", "theft"), "legal"), (("police", "report"), "legal"),
    (("ankle", "monitor"), "legal"), (("foster", "care"), "legal"),
    (("foster", "child"), "legal"), (("foster", "kid"), "legal"),
    (("foster", "home"), "legal"), (("juvenile", "court"), "legal"),
    (("juvenile", "detention"), "legal"), (("traffic", "court"), "legal"),
    (("small", "claims"), "legal"), (("murder", "trial"), "legal"),
    (("criminal", "trial"), "legal"), (("protective", "order"), "legal"),
    (("work", "permit"), "legal"), (("immigration", "status"), "legal"),
    (("credit", "score"), "money"), (("credit", "report"), "money"),
    (("food", "bank"), "money"), (("food", "pantry"), "money"),
    (("soup", "kitchen"), "money"), (("snap", "benefits"), "money"),
    (("social", "security"), "money"), (("disability", "benefits"), "money"),
    (("low", "income"), "money"), (("minimum", "wage"), "money"),
    (("payday", "loan"), "money"), (("loan", "shark"), "money"),
    (("debt", "collection"), "money"), (("past", "due"), "money"),
    (("unpaid", "bills"), "money"), (("tax", "lien"), "money"),
    (("wages", "garnished"), "money"), (("garnished", "wages"), "money"),
    (("sex", "worker"), "sex"), (("sex", "work"), "sex"),
    (("open", "marriage"), "sex"), (("open", "relationship"), "sex"),
    (("top", "surgery"), "sex"), (("bottom", "surgery"), "sex"),
    (("sugar", "daddy"), "sex"),
    (("hate", "crime"), "violence"), (("child", "neglect"), "violence"),
    (("black", "eye"), "violence"), (("sexual", "assault"), "violence"),
    (("organ", "transplant"), "health"), (("kidney", "transplant"), "health"),
    (("liver", "transplant"), "health"), (("heart", "transplant"), "health"),
    (("bone", "marrow"), "health"), (("pap", "smear"), "health"),
    (("ct", "scan"), "health"), (("heart", "attack"), "health"),
    (("had", "a", "stroke"), "health"), (("stroke", "recovery"), "health"),
    (("chronic", "pain"), "health"), (("pain", "meds"), "health"),
    (("pain", "management"), "health"), (("binge", "eating"), "health"),
    (("panic", "attack"), "health"), (("psych", "ward"), "health"),
    (("quit", "smoking"), "health"), (("quit", "drinking"), "health"),
    (("twelve", "step"), "health"), (("crack", "cocaine"), "health"),
    (("yom", "kippur"), "belief"), (("rosh", "hashanah"), "belief"),
    (("bar", "mitzvah"), "belief"), (("bat", "mitzvah"), "belief"),
    (("life", "support"), "grief"), (("last", "rites"), "grief"),
    (("terminal", "illness"), "grief"), (("terminally", "ill"), "grief"),
    (("in", "memory", "of"), "grief"),
    (("illegal", "immigrant"), "legal"), (("illegal", "immigrants"), "legal"),
    (("illegal", "alien"), "legal"), (("illegal", "aliens"), "legal"),
    # Pairs whose words are
    # ordinary on their own.
    (("gastric", "sleeve"), "health"), (("lap", "band"), "health"),
    (("tummy", "tuck"), "health"), (("c", "section"), "health"),
    (("egg", "freezing"), "health"), (("egg", "retrieval"), "health"),
    (("egg", "donor"), "health"), (("sperm", "donor"), "health"),
    (("sperm", "bank"), "health"), (("sperm", "count"), "health"),
    (("sperm", "sample"), "health"), (("surrogate", "mother"), "health"),
    (("gestational", "surrogate"), "health"), (("surrogate", "pregnancy"), "health"),
    (("hip", "replacement"), "health"), (("knee", "replacement"), "health"),
    (("joint", "replacement"), "health"), (("shoulder", "replacement"), "health"),
    (("radiation", "treatment"), "health"), (("radiation", "therapy"), "health"),
    (("breast", "implant"), "health"), (("breast", "augmentation"), "health"),
    (("breast", "reduction"), "health"), (("nose", "job"), "health"),
    (("plastic", "surgery"), "health"), (("cosmetic", "surgery"), "health"),
    (("weight", "loss", "surgery"), "health"), (("nursing", "home"), "health"),
    (("memory", "care"), "health"), (("assisted", "living"), "health"),
    (("morning", "after", "pill"), "health"), (("12", "step"), "health"),
    (("twelve", "steps"), "health"), (("sober", "living"), "health"),
    (("smoke", "weed"), "health"), (("smoking", "weed"), "health"),
    (("smokes", "weed"), "health"), (("smoked", "weed"), "health"),
    (("weed", "pen"), "health"), (("weed", "vape"), "health"),
    (("weed", "gummies"), "health"), (("weed", "edibles"), "health"),
    (("halfway", "house"), "legal"), (("protective", "services"), "legal"),
    (("child", "protective"), "legal"),
    (("strip", "club"), "sex"), (("dating", "app"), "sex"), (("dating", "site"), "sex"),
    (("tinder", "app"), "sex"), (("on", "tinder"), "sex"), (("tinder", "match"), "sex"),
    (("tinder", "profile"), "sex"),
    (("yells", "at", "me"), "violence"), (("yelled", "at", "me"), "violence"),
    (("yelling", "at", "me"), "violence"), (("screams", "at", "me"), "violence"),
    (("screamed", "at", "me"), "violence"), (("child", "grooming"), "violence"),
    (("online", "grooming"), "violence"), (("grooming", "victim"), "violence"),
    # More pairs whose words are
    # ordinary on their own.
    (("sickle", "cell"), "health"), (("cystic", "fibrosis"), "health"),
    (("cerebral", "palsy"), "health"), (("spina", "bifida"), "health"),
    (("multiple", "sclerosis"), "health"), (("kidney", "stones"), "health"),
    (("kidney", "stone"), "health"), (("feeding", "tube"), "health"),
    (("oxygen", "tank"), "health"), (("panic", "attacks"), "health"),
    (("premature", "ejaculation"), "health"), (("gamblers", "anonymous"), "health"),
    (("al", "anon"), "health"), (("planned", "parenthood"), "health"),
    (("fertility", "clinic"), "health"), (("mental", "hospital"), "health"),
    (("sober", "house"), "health"), (("group", "home"), "legal"),
    (("conversion", "therapy"), "sex"), (("pregnancy", "scare"), "health"),
    (("prep", "pills"), "health"), (("prep", "meds"), "health"),
    (("magic", "mushrooms"), "health"), (("shingles", "vaccine"), "health"),
    (("shingles", "shot"), "health"), (("sleep", "apnea"), "health"),
    (("yeast", "infection"), "health"), (("hep", "c"), "health"),
    (("spinal", "cord", "injury"), "health"), (("pregnancy", "test"), "health"),
    (("lithium", "level"), "health"), (("lithium", "levels"), "health"),
    (("lithium", "dose"), "health"), (("lithium", "pills"), "health"),
    (("taking", "lithium"), "health"), (("take", "lithium"), "health"),
    (("on", "lithium"), "health"), (("plan", "b", "pill"), "health"),
    (("gender", "reassignment"), "sex"), (("sex", "reassignment"), "sex"),
    (("gender", "affirming"), "sex"), (("gender", "affirmation"), "sex"),
    (("drag", "queen"), "sex"), (("sex", "toy"), "sex"), (("sex", "toys"), "sex"),
    (("nude", "beach"), "sex"), (("gay", "bar"), "sex"),
    (("plea", "deal"), "legal"), (("ice", "raid"), "legal"), (("ice", "detention"), "legal"),
    (("repo", "man"), "money"), (("collections", "agency"), "money"),
    (("payday", "lender"), "money"), (("pawn", "shop"), "money"),
    (("medical", "debt"), "money"), (("back", "taxes"), "money"),
    (("tax", "audit"), "money"), (("irs", "audit"), "money"),
    (("loan", "default"), "money"), (("crib", "death"), "grief"),
    (("catholic", "mass"), "belief"), (("jehovah", "witness"), "belief"),
    (("arrest", "warrant"), "legal"), (("bench", "warrant"), "legal"),
    (("search", "warrant"), "legal"),
    (("jehovahs", "witness"), "belief"), (("jehovahs", "witnesses"), "belief"),
    # More pairs whose words are
    # ordinary on their own.
    (("down", "syndrome"), "health"),
    (("hot", "flashes"), "health"), (("hot", "flash"), "health"),
    (("night", "sweats"), "health"), (("dna", "test"), "legal"),
    (("dna", "tests"), "legal"), (("dna", "results"), "legal"),
    (("donor", "eggs"), "health"), (("donor", "egg"), "health"),
    (("egg", "donation"), "health"), (("hair", "transplant"), "health"),
    (("hair", "transplants"), "health"), (("hair", "loss"), "health"),
    (("involuntary", "hold"), "health"), (("involuntary", "commitment"), "health"),
    (("psychiatric", "hold"), "health"), (("latter", "day", "saints"), "belief"),
    (("latter", "day", "saint"), "belief"), (("living", "will"), "grief"),
    (("low", "testosterone"), "health"), (("low", "t"), "health"),
    (("nicotine", "patch"), "health"), (("nicotine", "patches"), "health"),
    (("nicotine", "gum"), "health"), (("sugar", "baby"), "sex"),
    (("sugar", "babies"), "sex"), (("sugar", "mommy"), "sex"),
    (("wake", "service"), "grief"), (("funeral", "home"), "grief"),
    (("ward", "of", "the", "state"), "legal"), (("confirmation", "class"), "belief"),
    (("confirmation", "classes"), "belief"), (("section", "8", "housing"), "money"),
    (("section", "8", "voucher"), "money"), (("section", "8", "vouchers"), "money"),
    (("ms", "flare"), "health"), (("ms", "flares"), "health"),
    (("ms", "relapse"), "health"), (("als", "diagnosis"), "health"),
    (("womens", "shelter"), "violence"), (("women", "s", "shelter"), "violence"),
    (("homeless", "shelter"), "money"), (("family", "shelter"), "money"),
    # More pairs whose words are
    # ordinary on their own.
    (("heart", "bypass"), "health"), (("triple", "bypass"), "health"),
    (("quadruple", "bypass"), "health"), (("bypass", "surgery"), "health"),
    (("trial", "separation"), "legal"), (("legal", "separation"), "legal"),
    (("court", "ordered"), "legal"), (("anger", "management"), "health"),
    (("kill", "himself"), "health"), (("kill", "herself"), "health"),
    (("kill", "myself"), "health"), (("kill", "themselves"), "health"),
    (("killed", "himself"), "health"), (("killed", "herself"), "health"),
    (("killed", "myself"), "health"), (("killed", "themselves"), "health"),
    (("end", "his", "life"), "health"), (("end", "her", "life"), "health"),
    (("end", "my", "life"), "health"), (("ended", "his", "life"), "health"),
    (("ended", "her", "life"), "health"), (("took", "his", "own", "life"), "health"),
    (("took", "her", "own", "life"), "health"), (("take", "his", "own", "life"), "health"),
    (("take", "her", "own", "life"), "health"), (("hurt", "himself"), "health"),
    (("hurt", "herself"), "health"), (("hurting", "himself"), "health"),
    (("hurting", "herself"), "health"), (("blood", "test"), "health"),
    (("blood", "tests"), "health"), (("blood", "work"), "health"),
    (("blood", "draw"), "health"), (("strip", "clubs"), "sex"),
    # More pairs whose words
    # are ordinary on their own.
    (("night", "terrors"), "health"), (("night", "terror"), "health"),
    (("bed", "bugs"), "health"), (("bed", "bug"), "health"),
    (("macular", "degeneration"), "health"), (("puberty", "blockers"), "sex"),
    (("puberty", "blocker"), "sex"), (("tubes", "tied"), "health"),
    (("plan", "parenthood"), "health"), (("baby", "blues"), "health"),
    (("chewing", "tobacco"), "health"), (("blackout", "drunk"), "health"),
    (("aa", "sponsor"), "health"), (("sober", "coach"), "health"),
    (("smart", "recovery"), "health"), (("organ", "donor"), "grief"),
    (("insulin", "pump"), "health"), (("special", "ed"), "health"),
    (("couples", "therapy"), "sex"), (("couples", "counseling"), "sex"),
    (("couples", "counselling"), "sex"), (("marriage", "counseling"), "sex"),
    (("marriage", "counselling"), "sex"), (("family", "therapy"), "health"),
    (("survivor", "guilt"), "grief"), (("sitting", "shiva"), "grief"),
    (("after", "death"), "grief"), (("inheritance", "fight"), "money"),
    (("maxed", "out"), "money"), (("title", "loan"), "money"),
    (("title", "loans"), "money"), (("rent", "to", "own"), "money"),
    (("workers", "comp"), "money"), (("workers", "compensation"), "money"),
    (("union", "dues"), "belief"), (("green", "party"), "belief"),
    (("pro", "life"), "belief"), (("pro", "choice"), "belief"),
    (("sports", "betting"), "money"), (("lottery", "tickets"), "money"),
    (("one", "night", "stand"), "sex"), (("missing", "child"), "violence"),
    (("missing", "person"), "violence"), (("amber", "alert"), "violence"),
    (("supervised", "visits"), "legal"), (("supervised", "visitation"), "legal"),
    (("immigration", "lawyer"), "legal"), (("asylum", "hearing"), "legal"),
    (("bail", "bonds"), "legal"), (("bail", "bond"), "legal"),
    (("jail", "visit"), "legal"),
    (("food", "insecurity"), "money"), (("housing", "insecurity"), "money"),
    (("blood", "clots"), "health"), (("blood", "clot"), "health"),
    (("parental", "rights"), "legal"), (("petty", "theft"), "legal"),
    (("drug", "court"), "legal"), (("foster", "placement"), "legal"),
    (("kinship", "care"), "legal"), (("spousal", "support"), "legal"),
    (("public", "housing"), "money"), (("utility", "shutoff"), "money"),
    (("credit", "counseling"), "money"), (("voting", "record"), "belief"),
    (("campaign", "donations"), "belief"), (("union", "organizing"), "belief"),
    (("speaking", "in", "tongues"), "belief"), (("born", "again"), "belief"),
    (("morning", "sickness"), "health"), (("bed", "rest"), "health"),
    (("infant", "loss"), "grief"), (("death", "certificate"), "grief"),
    (("last", "will"), "grief"), (("crisis", "line"), "health"),
    (("psych", "hold"), "health"), (("binge", "drinking"), "health"),
    (("skin", "picking"), "health"), (("hair", "pulling"), "health"),
    (("speech", "delay"), "health"), (("feeding", "therapy"), "health"),
    (("embryo", "transfer"), "health"), (("reckless", "driving"), "legal"),
    (("dating", "apps"), "sex"), (("chest", "binding"), "sex"),
    (("testosterone", "shots"), "sex"), (("estrogen", "patches"), "sex"),
    (("pill", "mill"), "health"), (("inpatient", "stay"), "health"),
    (("diet", "pills"), "health"), (("heart", "murmur"), "health"),
)

#: Words that tell a private habit when they are hers and end the thought:
#: "because of my cutting", "my using and my drinking", "our weed". Never
#: when a thing follows ("my cutting board", "my weed killer"). Grammar
#: only, the same for every goal.
OWNED_HABITS = {"cutting": "health", "purging": "health", "bingeing": "health",
                "binging": "health", "using": "health", "smoking": "health",
                "weed": "health", "tinder": "sex", "surrogate": "health",
                "sperm": "health", "lithium": "health", "shingles": "health",
                "als": "health", "ms": "health", "crack": "health",
                # A bird too ("every thrush and robin").
                "thrush": "health"}

#: Plants and animals, whose diseases are a gardener's or a keeper's topic.
_PLANT_ANIMAL = frozenset("""plant plants leaf leaves tree trees lawn grass garden crop crops
tomato tomatoes rose roses fungal fruit vegetable vegetables orchid orchids houseplant
houseplants fish chicken chickens poultry livestock cattle goat goats horse horses bee bees
dog dogs cat cats pet pets bird birds""".split())
#: Listed words that are ordinary after these ("pet adoption", "cat
#: adoption", "study aids"): the word before says what it is about.
_ORDINARY_BEFORE = {
    "adoption": frozenset("""pet pets dog dogs cat cats kitten kittens puppy puppies animal
animals rescue shelter horse horses rabbit rabbits bird birds technology tech cloud user
early""".split()),
    "adoptions": frozenset("""pet pets dog dogs cat cats animal animals rescue shelter""".split()),
    # Things lent, not money owed ("book loans", "library loans").
    "loans": frozenset("""book books library libraries equipment tool tools gear art museum
interlibrary game games""".split()),
    # A plant's or an animal's trouble ("plant disease", "lawn disease",
    # "nitrogen deficiency").
    "disease": _PLANT_ANIMAL, "diseases": _PLANT_ANIMAL,
    "deficiency": _PLANT_ANIMAL | frozenset("nitrogen potassium phosphorus nutrient".split()),
    "deficiencies": _PLANT_ANIMAL | frozenset("nitrogen potassium phosphorus nutrient".split()),
    # Where a camper plugs in ("full hookups", "RV hookup").
    "hookups": frozenset("full rv water electric electrical sewer cable campsite".split()),
    "hookup": frozenset("full rv water electric electrical sewer cable campsite".split()),
    # A machine's grease ("chain lube"), not the bedroom.
    "lube": frozenset("""chain bike bicycle gun grease motor engine gear lock zipper drawer
car dry wet oil bearing""".split()),
}


def _ordinary_here(w: str, prev: str) -> bool:
    """Is the listed word *w* ordinary after *prev* ("study aids", "pet adoption")?"""
    p = _base(prev)
    if w == "aids":
        return p in _AIDS_ORDINARY_BEFORE
    return p in _ORDINARY_BEFORE.get(w, ())
#: "aids" is ordinary after these ("study aids", "visual aids").
_AIDS_ORDINARY_BEFORE = frozenset("""study visual teaching learning memory audio training
navigation writing reading math""".split())

MONTHS = frozenset("""january february march april june july august september october
november december jan feb mar apr jun jul aug sep sept oct nov dec""".split())
WEEKDAYS = frozenset("""monday tuesday wednesday thursday friday saturday sunday mondays
tuesdays wednesdays thursdays fridays saturdays sundays""".split())
SEASONS = frozenset("spring summer autumn fall winter".split())
#: "by fall", "during the fall", "before spring".
SEASON_CUES = frozenset("""next this last in every each since until by during till before after
around""".split())

# The time of her own plans is personal ("summer" must not go out as "family
# budget summer trip" for "saving for a summer trip to visit my parents"):
# seasons, holidays and weekends in her goal are held words that can never be
# ticked, never sent unless they sit in the bot's subject ("a summer camp
# finder bot", "a Christmas cookie bot"). Held, not hard, so a job named after
# one ("plan for Christmas") is still searched by its other words. Months and
# weekdays were hard already and still never join the subject.

#: Seasons that are only ever a time of year.
PLAIN_SEASONS = frozenset("summer summers autumn autumns winter winters".split())
#: "spring" and "fall" are also everyday words ("spring rolls", "fall
#: asleep"): a time only after a cue ("next spring") or right before one of
#: these plan words ("a fall wedding", "spring break").
PLAN_NOUNS = frozenset("""trip trips vacation vacations break breaks holiday holidays getaway
getaways visit visits wedding weddings party parties semester semesters term terms season
seasons camp camps move cruise cruises recess recesses""".split())
#: Holidays and other days of the year (one word each).
HOLIDAYS = frozenset("""christmas christmases xmas christmastime thanksgiving thanksgivings
easter easters halloween halloweens hanukkah chanukah kwanzaa diwali passover ramadan eid
valentine valentines juneteenth hogmanay advent weekend weekends
holiday holidays holidaytime summertime wintertime springtime autumntime falltime
midsummer midsummers midwinter solstice solstices equinox equinoxes yule yuletide
eastertide christmastide friendsgiving oktoberfest purim sukkot shavuot nowruz navratri
vesak holi lohri pongal onam songkran quinceanera quinceaneras nye hols""".split())
#: Words that name a stretch of time when they follow a season or holiday
#: ("summer vacation", "Christmas break", "holiday season", "Christmas Eve",
#: "summer-time"): the time of her plans too.
PERIOD_NOUNS = frozenset("""break breaks vacation vacations vacay hols season seasons eve
time times day days night nights week weeks weekend weekends recess recesses""".split())
#: Capitalised words that end the name of a day of the year ("Veterans Day").
_DAY_HEADS = frozenset("Day Days Eve Weekend".split())
#: Seasons and holidays long enough to be read inside a glued word
#: ("summer2026", "christmasbreak").
_GLUE_TIMES = ("summer", "winter", "autumn", "christmas", "easter", "halloween",
               "thanksgiving", "holiday", "hanukkah", "passover", "ramadan", "diwali")
#: The time inside a held glued word ("springtime", "midsummer", "yuletide",
#: "summercamp"), held on its own too.
_GLUE_PIECES = _GLUE_TIMES + ("spring", "fall", "xmas", "yule", "easter", "weekend")
#: Days of the year named by two or three words, and the words of each that
#: only that day uses (held on their own too). Empty when no word is only
#: the day's ("new year": both words are everyday words); the whole run is
#: still held.
DATE_PHRASES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("new", "year"), ()), (("new", "years"), ()), (("memorial", "day"), ("memorial",)),
    (("labor", "day"), ("labor",)), (("labour", "day"), ("labour",)),
    (("independence", "day"), ("independence",)), (("veterans", "day"), ("veterans",)),
    (("presidents", "day"), ("presidents",)), (("boxing", "day"), ("boxing",)),
    (("patricks", "day"), ("patricks",)), (("patrick", "s", "day"), ("patrick",)),
    (("mothers", "day"), ()), (("mother", "s", "day"), ()), (("fathers", "day"), ()),
    (("father", "s", "day"), ()), (("valentines", "day"), ()),
    (("valentine", "s", "day"), ()), (("mardi", "gras"), ("mardi", "gras")),
    (("cinco", "de", "mayo"), ("cinco", "mayo")), (("groundhog", "day"), ("groundhog",)),
    (("spring", "break"), ("spring",)), (("black", "friday"), ()),
    (("cyber", "monday"), ()), (("earth", "day"), ()),
    # Every word of a day of the year is held, so these hold "good", "super",
    # "long", "lunar" too.
    (("good", "friday"), ()), (("super", "bowl"), ()), (("long", "weekend"), ()),
    (("lunar", "new", "year"), ()), (("chinese", "new", "year"), ()),
    (("new", "year", "eve"), ()), (("new", "years", "eve"), ()),
    (("christmas", "eve"), ()), (("holiday", "season"), ()), (("bank", "holiday"), ()),
    (("martin", "luther", "king"), ()), (("mlk", "day"), ()), (("pi", "day"), ()),
    (("april", "fools"), ()), (("day", "of", "the", "dead"), ()),
    (("all", "saints"), ()), (("rosh", "hashanah"), ()), (("yom", "kippur"), ()),
    (("fourth", "of", "july"), ()), (("st", "patrick"), ()), (("saint", "patrick"), ()),
    (("st", "patricks"), ()), (("saint", "patricks"), ()),
    # "Turkey Day", "May Day", "the end of the school year", "back to
    # school", "half-term", and the model's other words for them.
    (("turkey", "day"), ()), (("may", "day"), ()), (("new", "years", "day"), ()),
    (("end", "of", "the", "school", "year"), ()), (("end", "of", "school", "year"), ()),
    (("start", "of", "the", "school", "year"), ()), (("start", "of", "school"), ()),
    (("end", "of", "school"), ()), (("last", "day", "of", "school"), ()),
    (("first", "day", "of", "school"), ()), (("back", "to", "school"), ()),
    (("school", "holidays"), ()), (("school", "holiday"), ()), (("school", "break"), ()),
    (("school", "vacation"), ()), (("half", "term"), ()), (("new", "year", "s", "eve"), ()),
    (("new", "year", "s", "day"), ()),
)

#: Without this, "trip in the fall" sends the model's "autumn packing" and
#: "Turkey Day" sends "thanksgiving". Every word
#: that names a time of year, a month, a day or a holiday. When the goal
#: names a time of her own plans (``PersonalWords.plan_time``), each of these
#: is held in every phrase that leaves, whatever word she or a model used
#: for it, unless it is a word of the bot's subject.
TIME_WORDS = frozenset(SEASONS | PLAIN_SEASONS | HOLIDAYS | MONTHS | WEEKDAYS | frozenset(
    """autumnal wintry summery vernal springy yuletide noel hols vacay weekend weekends
    semester semesters trimester trimesters halfterm midterm midterms may xmastime newyear
    newyears nye eve memorial labor labour independence veterans presidents boxing patricks
    mardi gras cinco mayo groundhog kippur hashanah thanksgivings""".split()))
#: Runs of words that name such a time, held in every phrase that leaves
#: under the same rule. Every day of the year above, and school times.
TIME_SEQS: tuple[tuple[str, ...], ...] = tuple(seq for seq, _own in DATE_PHRASES) + (
    ("school", "year"), ("school", "years"), ("school", "term"), ("new", "year"),
    ("new", "years"), ("fourth", "of", "july"), ("4th", "of", "july"), ("july", "4th"),
    ("end", "school"), ("spring", "break"), ("fall", "break"), ("winter", "break"),
    ("summer", "break"), ("summer", "holiday"), ("summer", "holidays"), ("turkey", "day"),
    ("long", "weekend"), ("bank", "holiday"), ("half", "term"), ("labour", "day"),
    ("labor", "day"))
#: Plan words that tell a trip taken at that time ("a summer trip", "our
#: Xmas getaway"): held after a time of her plans like the time itself.
TRAVEL_NOUNS = frozenset("""trip trips getaway getaways cruise cruises visit visits
vacation vacations staycation staycations roadtrip roadtrips holiday holidays""".split())
#: Words after a season that keep it a time of her plans ("our summer
#: plans", "the winter schedule"); another word after it names a kind of
#: thing ("a winter scarf", "autumn leaf peeping").
PLAN_AFTER = frozenset("""plan plans planning schedule schedules calendar calendars itinerary
itineraries travel travels activities activity routine routines budget budgets
packing""".split())

#: Counting words. "one" is left out on purpose: it counts nobody in
#: practice ("all in one", "one page") and holding it would drop many
#: harmless phrases.
NUMBER_WORDS = frozenset("""two three four five six seven eight nine ten eleven twelve
thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty
sixty seventy eighty ninety hundred dozen""".split())

#: Joining words that start a place: "in Tucson", "over Dayton", "to Mexico City".
PLACE_CUES = frozenset("in at near from to over around outside visiting".split())
NAME_CUES = frozenset("named called nicknamed".split())

#: Compass regions: a place in all but name. Held only when the goal holds a
#: place (spec rule 4); never allowed out by a tick. The spec's list plus the
#: other compass points and their adjectives (more restrictive, noted).
REGION_WORDS = frozenset("""north south east west midwest southwest northwest northeast
southeast northern southern eastern western midwestern southwestern northwestern
northeastern southeastern""".split())
REGION_PHRASES = (("pacific", "northwest"), ("gulf", "coast"), ("new", "england"))

#: Postal codes of US states and territories and Canadian provinces, so her
#: home state is held whichever way the household place spells it ("OH" and
#: "Ohio" both hold "ohio").
STATE_CODES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas",
    "ca": "california", "co": "colorado", "ct": "connecticut", "de": "delaware",
    "dc": "district of columbia", "fl": "florida", "ga": "georgia", "hi": "hawaii",
    "id": "idaho", "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas",
    "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland",
    "ma": "massachusetts", "mi": "michigan", "mn": "minnesota", "ms": "mississippi",
    "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada",
    "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico", "ny": "new york",
    "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma",
    "or": "oregon", "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina",
    "sd": "south dakota", "tn": "tennessee", "tx": "texas", "ut": "utah",
    "vt": "vermont", "va": "virginia", "wa": "washington", "wv": "west virginia",
    "wi": "wisconsin", "wy": "wyoming", "pr": "puerto rico", "gu": "guam",
    "vi": "virgin islands", "as": "american samoa", "mp": "northern mariana islands",
    "ab": "alberta", "bc": "british columbia", "mb": "manitoba", "nb": "new brunswick",
    "nl": "newfoundland and labrador", "ns": "nova scotia", "nt": "northwest territories",
    "nu": "nunavut", "on": "ontario", "pe": "prince edward island", "qc": "quebec",
    "sk": "saskatchewan", "yt": "yukon",
}
_STATE_NAMES = {name: code for code, name in STATE_CODES.items()}

#: Languages are never a personal detail by themselves.
LANGUAGES = frozenset("""english spanish french german italian portuguese dutch russian
chinese mandarin cantonese japanese korean arabic hindi urdu bengali punjabi turkish greek
hebrew polish swedish norwegian danish finnish czech hungarian romanian ukrainian vietnamese
thai indonesian malay tagalog swahili persian farsi latin irish welsh icelandic catalan
basque""".split())

#: Diet words: said about a person they are health details (spec rule 5).
DIET_WORDS = frozenset("gluten dairy nut nuts peanut peanuts halal kosher vegan".split())

#: What names a bot. The subject is read in front of the last one.
BOT_NOUNS = frozenset("bot bots app apps helper helpers buddy buddies assistant "
                      "assistants tutor tutors coach coaches".split())
#: A bot noun that is only a second name for the bot when it sits right in
#: front of the last one ("a garden helper bot").
GENERIC_BOT_WORDS = frozenset("helper helpers assistant assistants buddy buddies app apps".split())
#: Where the subject stops, reading backwards from the bot noun.
SUBJECT_STOPS = frozenset("a an my the build make want need".split())
#: Words that describe how a bot is, not what it is about.
SUBJECT_FILLER = frozenset("""a an the some little new simple cool fun smart own kind sort
just really very good great nice awesome super tiny basic easy helpful friendly of for with
to that which who this that these those me us named called""".split())

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’-]*(?:&[A-Za-z0-9]+)*|&")
_SENT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
#: Where a name cue may NOT reach across: only the end of a sentence. A line
#: break inside a sentence ("my daughter\nRose", "Grace\nis my daughter")
#: is only a space for the cues.
_CUE_SENT_RE = re.compile(r"(?<=[.!?])\s+")
_AGE_RE = re.compile(r"\d+-?(?:year|yr|y)s?(?:-?old)?|\d+(?:yo|yrs?|mo|mos|months?)")
MAX_GOAL = 2000

# --- the dictionary --------------------------------------------------------------

_DICT: dict[str, str] | None = None
#: Word forms the list knows both as an ordinary word and as a name ("rose",
#: "mark", "grace", "will"): filled with _DICT.
_ALSO_NAMES: set[str] = set()
#: Inflected form -> the ordinary words the list made it from with its own
#: plural, past and "-ing" flags ("relapsing": {"relapse"}, "gravies":
#: {"gravy"}). Filled with _DICT.
_INFLECTED_FROM: dict[str, set[str]] = {}
_DICT_LOCK = threading.Lock()


def _dict_path() -> str:
    """The shipped list, found next to the code first (so a changed
    ``REPO_ROOT`` in one caller cannot leave the list unread for all)."""
    here = os.path.dirname(os.path.abspath(__file__))
    beside = os.path.normpath(os.path.join(here, "..", "..", "..", "config",
                                           "wordlists", "en_US.dic"))
    if os.path.isfile(beside):
        return beside
    from puffpai.catalogs import live
    return os.path.join(live.repo_root(), "config", "wordlists", "en_US.dic")


def _word_forms(word: str, flags: str) -> list[str]:
    """*word* plus the forms its SCOWL flags make (lower case).

    Only the three suffix flags that change which words exist are expanded,
    with the en_US affix file's own rules: ``S`` (plural: "numbers",
    "ideas", "batteries"), ``D`` (past: "baked", "carried") and ``G``
    ("-ing": "baking"). ``M`` (the possessive) is not needed: a trailing
    "'s" is taken off before any lookup.
    """
    w = word.lower()
    out = [w]
    if "S" in flags:
        if re.search(r"[^aeiou]y$", w):
            out.append(w[:-1] + "ies")
        elif re.search(r"[sxzh]$", w):
            out.append(w + "es")
        else:
            out.append(w + "s")
    if "D" in flags:
        if w.endswith("e"):
            out.append(w + "d")
        elif re.search(r"[^aeiou]y$", w):
            out.append(w[:-1] + "ied")
        else:
            out.append(w + "ed")
    if "G" in flags:
        out.append(w[:-1] + "ing" if w.endswith("e") else w + "ing")
    return out


def _load_dict() -> dict[str, str]:
    """SCOWL en_US: lower-case word form -> ``common`` or ``proper``.

    A word the list only knows capitalised is proper ("Tucson"); an
    all-capital entry is an acronym (API, GPS, ISS) and counts as common.
    Plural, past and "-ing" forms come from each entry's flags, so
    "numbers" is common (from "number/ASDG") although the list also has the
    proper "Numbers". When a form is both, common wins ("mark", "rose").
    A missing or unreadable list gives an empty dictionary: every word is
    then ``unknown`` and nothing is exempted by it (fail closed: unknown
    capitalised words are still held as brands).
    """
    out: dict[str, str] = {}
    proper: set[str] = set()
    made_from: dict[str, set[str]] = {}
    try:
        with open(_dict_path(), encoding="utf-8", errors="replace") as fh:
            next(fh, None)
            for line in fh:
                word, _, flags = line.strip().partition("/")
                word = word.strip()
                if not word or not word.isalpha():
                    continue
                kind = ("proper" if word[0].isupper()
                        and not (len(word) > 1 and word.isupper()) else "common")
                if kind == "proper":
                    proper.add(word.lower())
                forms = _word_forms(word, flags)
                for form in forms:
                    if kind == "common" or form not in out:
                        out[form] = kind
                if kind == "common":
                    for form in forms[1:]:
                        made_from.setdefault(form, set()).add(forms[0])
    except OSError:
        return {}
    _INFLECTED_FROM.clear()
    _INFLECTED_FROM.update(made_from)
    _ALSO_NAMES.clear()
    _ALSO_NAMES.update(w for w in proper if out.get(w) == "common")
    return out


def _dictionary() -> dict[str, str]:
    global _DICT
    if _DICT is None:
        with _DICT_LOCK:
            if _DICT is None:
                _DICT = _load_dict()
    return _DICT


def dict_kind(word: str) -> str:
    """``common``, ``proper`` (a name or place in the list) or ``unknown``.

    Function words ("as", "us", "it") are common whatever the list says (it
    has only the chemical symbol "As").
    """
    w = _base(word)
    d = _dictionary()
    if w in d:
        if d[w] == "proper" and w in goal_words.stopwords():
            return "common"
        return d[w]
    if w in goal_words.stopwords():
        return "common"
    got = d.get(goal_words.singular(w))
    if got:
        return got
    # A verb the list keeps only in its other forms: it has "rehearsed" and
    # "rehearsing" but not "rehearse" (without this, "rehearse" reads as
    # "re" + "hearse" and makes a band goal a private story).
    if len(w) > 4 and w.endswith("e") and d.get(w + "d") == "common" \
            and d.get(w[:-1] + "ing") == "common":
        return "common"
    # Or only as a word grown from it ("reminder", "reminded", "reminds" for
    # "remind"): a capital "Remind me ..." at the start of a sentence is not
    # a name.
    if len(w) >= 4 and w.isalpha() and any(
            d.get(w + end) == "common" for end in ("s", "es", "ed", "ing", "er", "ers")):
        return "common"
    return "unknown"


def is_name_like(word: str) -> bool:
    """Could *word*, typed in lower case, be a person's name? True when the
    list knows it as a name at all ("rose", "mark", "tucson") or not at all
    ("jayden"); False for an ordinary word only ("piano", "fractions")."""
    w = _base(word)
    if not w:
        return False
    d = _dictionary()
    if not d:
        return True                       # no list: fail closed
    kind = dict_kind(w)
    return kind != "common" or w in _ALSO_NAMES or goal_words.singular(w) in _ALSO_NAMES


def is_common_word(word: str) -> bool:
    """Is *word* an ordinary dictionary word? (``False`` when the list is missing.)"""
    return _dictionary().get(_base(word)) == "common"


# --- helpers -------------------------------------------------------------------

def _fold(text: object, *, other_letters: str = "look") -> str:
    """The goal with accented letters spelled plainly, cut to :data:`MAX_GOAL`
    ("Noémie" is one word, "noemie", never "no" and "mie").
    A word broken over a line with a hyphen ("Rose-" + line break + "Marie")
    is one word again (so "rose" is not searched). A word
    spelled out one letter at a time ("l i a m", "L.I.A.M") is one word again
    (so "liam chores" does not go out).
    *other_letters*: see :func:`goal_words.fold_text`."""
    text = goal_words.fold_text(str(text or "")[:MAX_GOAL * 2], other_letters=other_letters)
    text = _LINE_HYPHEN_RE.sub("-", text)
    text = _SPELLED_OUT_RE.sub(lambda m: re.sub(r"[ ._-]", "", m.group(0)), text)
    text = _SHOUTED_RE.sub(_unshout, text)
    return text[:MAX_GOAL]


#: A sentence or line (with what ends it).
_SHOUTED_RE = re.compile(r"[^.!?\n\r]+[.!?\n\r]*")


def _unshout(m: "re.Match[str]") -> str:
    """A sentence typed all in capitals ("LIAM NEEDS A CHORES BOT") in lower
    case, so the rules that read capitals (names, brands) read it as words
    and the lower-case name rules apply (so "liam chores" does not go
    out). Only a sentence of three or more words
    of two letters or more and no lower-case letter at all; the length never
    changes, so offsets stay true."""
    seg = m.group(0)
    if any(c.islower() for c in seg) or len(re.findall(r"[A-Za-z]{2,}", seg)) < 3:
        return seg
    low = seg.lower()
    return low if len(low) == len(seg) else seg


#: A hyphen at the end of a line, with the next line's word after it.
_LINE_HYPHEN_RE = re.compile(r"(?<=[A-Za-z])-[ \t]*(?:\r\n|\r|\n)[ \t\r\n]*(?=[A-Za-z])")
#: Three or more single letters in a row, each after one space, dot, hyphen
#: or underscore ("l i a m", "L.I.A.M", "l-i-a-m"): a word spelled out.
_SPELLED_OUT_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z](?:[ ._-][A-Za-z]){2,}(?![A-Za-z0-9])")


#: Characters that make the text after them show right to left (override,
#: embedding, isolate), and the ones that end such a run.
_RTL_OPEN = frozenset("‮‫⁧")
_RTL_CLOSE = frozenset("‬⁩\n\r")


def _right_to_left_readings(text: str) -> dict[str, set[str]]:
    """``{word as stored: {words as shown}}`` for each word a right-to-left
    control turns around ("L" + U+202E + "maiL" is stored "LmaiL" but shows
    "LLiam"): the word as shown, and the turned-around run on its own.

    Without this, an RTL-override spelling lets a model's "liam soccer" out.
    Used only to HOLD more."""
    out: dict[str, set[str]] = {}
    i = 0
    n = len(text)
    while i < n:
        if text[i] not in _RTL_OPEN:
            i += 1
            continue
        end = i + 1
        while end < n and text[end] not in _RTL_CLOSE:
            end += 1
        run = text[i + 1:end]
        # The whole word around the control: letters before it, the run.
        start = i
        while start > 0 and (text[start - 1].isalnum() or goal_words._invisible(text[start - 1])):
            start -= 1
        stored = text[start:end]
        shown = text[start:i] + run[::-1]

        def plain(t: str) -> list[str]:
            return [re.sub(r"[^a-z0-9]", "", w) for w in _fold(t).lower().split()]

        stored_words = [w for w in plain(stored) if w]
        shown_words = {w for w in plain(shown) + plain(run[::-1]) if len(w) >= 2}
        for w in stored_words:
            got = shown_words - {w}
            if got:
                out.setdefault(w, set()).update(got)
        i = end
    return out


def other_spellings(goal: object) -> dict[str, set[str]]:
    """Each word of *goal* written with letters of another alphabet, read the
    other ways it can be read: ``{word as it looks: {by sound, with those
    letters dropped}}`` ("maria": {"magia", "maia"} for "Maгia"; "3oe":
    {"zoe", "oe"} for "Зoe"). Empty for a plain goal. Without it, a
    model reads "Maгia" as "magia" and sends it. Used
    only to HOLD more: a held name holds every reading of it."""
    text = goal_words.decode_encoded(str(goal or "")[:MAX_GOAL * 2])
    if text.isascii():
        return {}
    out: dict[str, set[str]] = {}
    for look, others in _right_to_left_readings(text).items():
        out.setdefault(look, set()).update(others)
    for tok in re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text):
        if tok.isascii():
            continue
        def plain(mode: str) -> str:
            # The word whole, lower case ("DeShawn": "deshawn", not split).
            return re.sub(r"[^a-z0-9]", "", _fold(tok, other_letters=mode).lower())
        look = plain("look")
        if not look:
            continue
        others = {got for got in (plain("sound"), plain("drop"))
                  if got != look and len(got) >= 2}
        if others:
            out.setdefault(look, set()).update(others)
    return out


def _base(word: str) -> str:
    """Lower case, curly quote folded, possessive taken off."""
    w = str(word or "").replace("’", "'").lower().strip("'")
    if w.endswith("'s"):
        w = w[:-2]
    # A hyphen at either end joins nothing ("Rose-" at the end of a line).
    return w.strip("-'")


def _forms(word: str) -> set[str]:
    w = _base(word)
    return {w, goal_words.singular(w)} - {""}


def _tokens(text: str) -> list[str]:
    return [t.replace("’", "'") for t in _TOKEN_RE.findall(str(text or ""))]


def _sentences(goal: str) -> list[str]:
    return [s for _, s in _spans(goal, _SENT_RE)]


def _spans(goal: str, regex: "re.Pattern[str]") -> list[tuple[int, str]]:
    """(offset in the goal, sentence) for each non-empty piece split by *regex*."""
    text = str(goal or "")[:MAX_GOAL]
    out: list[tuple[int, str]] = []
    start = 0
    for m in regex.finditer(text):
        if text[start:m.start()].strip():
            out.append((start, text[start:m.start()]))
        start = m.end()
    if text[start:].strip():
        out.append((start, text[start:]))
    return out


def _is_cap(tok: str) -> bool:
    return bool(tok) and tok[0].isupper()


def _has_digit(word: str) -> bool:
    return any(ch.isdigit() for ch in word)


def _tok_seps(text: str, offset: int = 0) -> tuple[list[str], list[str], list[int]]:
    """Tokens of *text*, for each the text between it and the one before (so
    "Grace, my daughter" knows there is a comma after "Grace"), and each
    token's offset in the whole goal (*offset* is where *text* starts)."""
    toks: list[str] = []
    seps: list[str] = []
    starts: list[int] = []
    end = 0
    for m in _TOKEN_RE.finditer(str(text or "")):
        toks.append(m.group(0).replace("\u2019", "'"))
        seps.append(text[end:m.start()])
        starts.append(offset + m.start())
        end = m.end()
    return toks, seps, starts


def _is_age(toks: list[str], j: int) -> bool:
    """An age at *j*: "8-year-old", "10yo", or the "old" of "8 year old"."""
    if j < 0 or j >= len(toks):
        return False
    b = _base(toks[j])
    if _AGE_RE.fullmatch(b):
        return True
    return b == "old" and j >= 1 and _base(toks[j - 1]) in (
        "year", "years", "yr", "yrs", "month", "months")


#: What may follow "<Name> is 8" for it to be an age ("Rose is 8 and ...").
_AFTER_AGE = frozenset("""and or but so now soon next this today in on year years yr yrs
month months old""".split())


def _is_count(tok: str) -> bool:
    b = _base(tok)
    return (b.isdigit() and int(b) < 130) or b in NUMBER_WORDS or bool(_AGE_RE.fullmatch(b))


def _age_follows(toks: list[str], seps: list[str], i: int) -> bool:
    """Does an age follow the word at *i*: "Rose, 8,", "Rose is 8 and",
    "Rose turns 8", "Rose just turned 8"?"""
    j = i + 1
    if j >= len(toks):
        return False
    if "," in seps[j]:
        return _is_count(toks[j]) and (j + 1 >= len(toks) or "," in seps[j + 1]
                                       or _base(toks[j + 1]) in _AFTER_AGE)
    verb = _base(toks[j])
    if verb == "just" and j + 1 < len(toks):
        j += 1
        verb = _base(toks[j])
    if verb in ("turns", "turned", "turning") and j + 1 < len(toks):
        return _is_count(toks[j + 1])
    if verb in ("is", "was") and j + 1 < len(toks):
        k = j + 1
        if _base(toks[k]) in ("turning", "now", "only", "almost", "nearly") and k + 1 < len(toks):
            k += 1
        if not _is_count(toks[k]):
            return False
        return k + 1 >= len(toks) or "," in seps[k + 1] or _base(toks[k + 1]) in _AFTER_AGE
    return False


def _owned(toks: list[str], j: int) -> bool:
    """Is the word at *j* made hers by a my/our, a count or an age in front of
    it, with at most two describing words between ("my little girl", "our
    two old dogs")? A joining word ("for", "the") ends the look back."""
    for k in range(j - 1, max(-1, j - 4), -1):
        b = _base(toks[k])
        if b in POSSESSIVES or b in NUMBER_WORDS or b.isdigit() or _is_age(toks, k):
            return True
        if b in FUNCTION_WORDS or b in BOT_NOUNS or not b:
            return False
    return False


def _kin(word: str) -> bool:
    """A relative, also inside a joined word ("step-daughter", "stepdaughter")."""
    b = _base(word)
    if not b:
        return False
    if _forms(b) & RELATIVES:
        return True
    parts = [x for x in re.split(r"-", b) if x]
    return len(parts) > 1 and bool(_forms(parts[-1]) & RELATIVES)


def _person_at(toks: list[str], j: int) -> bool:
    """Is the word at *j* one of her people or pets, so that the next word may
    be their name? Relatives always ("sister Anna", "my step-daughter Rose");
    an age ("my 8-year-old Grace"); kids, pets and the like when they are hers
    ("my little girl Rose", "our puppy Biscuit"). A relative's own thing ("my
    son's soccer team") is not a name, so a possessive does not count.
    """
    if j < 0 or j >= len(toks):
        return False
    raw = toks[j].lower()
    if raw.endswith("'s") or raw.endswith("'"):
        return False
    if _kin(raw) or _is_age(toks, j):
        return True
    if _forms(raw) & (AUDIENCE | BEINGS):
        return _owned(toks, j)
    return False


def _person_ahead(toks: list[str], p: int) -> bool:
    """Does a my/our at *p* start one of her people ("my daughter", "my
    little girl", "our dog", "my 8-year-old")?"""
    if p >= len(toks) or _base(toks[p]) not in POSSESSIVES:
        return False
    for k in range(p + 1, min(len(toks), p + 4)):
        b = _base(toks[k])
        if _kin(b) or _is_age(toks, k) or _forms(b) & (AUDIENCE | BEINGS):
            return True
        if b in FUNCTION_WORDS or b in BOT_NOUNS:
            return False
    return False


#: Words that open a clause, never a name ("our family because ...").
_CLAUSE_OPENERS = frozenset("""because since while although though unless until whereas once
cause cuz""".split())


#: Joining words of place and time that never stand for a person ("my
#: sister after chemo").
_NEVER_NAMES = frozenset("""after before during since around across against along among
behind below beneath beside besides between beyond despite except inside near outside
through toward towards upon within without like unlike versus""".split())


def _name_strength(tok: str, *, cued: bool) -> str:
    """How surely a word sitting where a name goes is one.

    ``strong``: capitalised, not a dictionary word, or given by an explicit
    cue ("named", "her name is", "Grace, my daughter"); such a word is held
    and never part of the subject. ``weak``: a lower-case ordinary word
    ("my son soccer coach bot"); held, but it stays in the subject when it
    sits there. ``""``: a joining word, a hard word or a bot noun.
    """
    b = _base(tok)
    if (not b or b in FUNCTION_WORDS or b in ("i", "i'm", "im") or _has_digit(b)
            or b in _CLAUSE_OPENERS or b in _NEVER_NAMES):
        return ""
    if is_hard_word(b) or b in BOT_NOUNS or _forms(b) & (AUDIENCE | BEINGS | RELATIVES):
        return ""
    if _is_cap(tok):
        return "strong"          # "my son Will": a name even when it is a stop word
    if b in goal_words.stopwords() or b in SUBJECT_FILLER:
        return ""
    if cued or dict_kind(b) != "common":
        return "strong"
    return "weak"


#: Words after which a lower-case word that is not an ordinary word is a
#: person's name: "a party planner bot for emma", "a chess game with noah".
_LOWER_NAME_CUES = frozenset("for with".split())


#: Past forms the spelling list's flags do not make.
_IRREGULAR_PAST = frozenset("""went got had made came took left ran began felt knew said told
gave found kept lost won grew wrote broke became fell forgot bought brought thought taught
caught sold spent sent built drove rode swam sang ate slept met paid heard led read stood
understood quit""".split())


def _has_verb_forms(base: str) -> bool:
    """Does the spelling list make a past or "-ing" form of *base*?"""
    stem = base[:-1] if base.endswith("e") else base
    forms = {base + "ed", base + "d", base + "ing", stem + "ing"}
    if base.endswith("y"):
        forms.add(base[:-1] + "ied")
    if len(base) > 2:
        forms |= {base + base[-1] + "ing", base + base[-1] + "ed"}   # "forgetting"
    d = _dictionary()
    return any(base in _INFLECTED_FROM.get(f, ()) or d.get(f) == "common" for f in forms)


def _verb_like(word: str) -> bool:
    """Is lower-case *word* a verb form by the spelling list ("plays",
    "joined", "started", "went")? Never a word the list also knows
    as a name ("will", "grace", "mark", "hope"). ``False`` with no list."""
    w = _base(word)
    d = _dictionary()
    if not d or not w.isalpha() or w in _ALSO_NAMES or d.get(w) != "common":
        return False
    if w in _IRREGULAR_PAST:
        return True
    # Only a form the list makes with an ending: a plain word ("river",
    # "sky", "sage") may be a given name and stays held.
    if not w.endswith(("s", "ed", "ing")):
        return False
    bases = {b for b in _INFLECTED_FROM.get(w, ()) if b != w}
    if bases and not w.endswith("s"):
        return True
    # An "-s" form is a verb only when the list also makes a past or "-ing"
    # form of its base ("plays": "played"), never a plural noun only
    # ("fractions", which may be a word she uses as a name: held).
    if any(_has_verb_forms(b) for b in bases):
        return True
    if w.endswith("s"):
        return False
    # A form the list carries as its own entry ("skipped"): its stem is an
    # ordinary word too ("skip").
    stems: set[str] = set()
    for end in ("ed", "ing", "es", "s"):
        if w.endswith(end) and len(w) - len(end) >= 3:
            b = w[:-len(end)]
            stems |= {b, b + "e"}
            if len(b) > 2 and b[-1] == b[-2]:
                stems.add(b[:-1])
    return any(d.get(b) == "common" and b not in _ALSO_NAMES for b in stems)


def _plain_word(b: str) -> bool:
    """A word that is never a name by itself: joining, filler, a language,
    a bot noun, a relative, a pet or audience word, a hard or sensitive word."""
    return (not b or not b.isalpha() or b in FUNCTION_WORDS or b in goal_words.stopwords()
            or b in SUBJECT_FILLER or b in LANGUAGES or b in BOT_NOUNS
            or bool(_forms(b) & (AUDIENCE | BEINGS | RELATIVES))
            or bool(is_hard_word(b)) or bool(sensitive_category(b)))


def _lower_name(toks: list[str], seps: list[str], i: int) -> str:
    """How surely a lower-case word right after "for"/"with" is a name.

    Without this, "a party planner bot for emma" holds nothing and "emmas
    birthday party" is searched. ``strong``: the
    spelling list knows it only as a name ("emma", "noah") or not at all
    ("jayden", and tech words such as "kubernetes": held too, fail closed;
    she can tick it). ``weak``: an ordinary word that is also a name
    ("grace", "mark") at the end of its clause ("a bot for grace."). ``""``
    otherwise ("for beginners", "for school", "for bill payments").
    """
    tok = toks[i]
    b = _base(tok)
    if _is_cap(tok) or _plain_word(b):
        return ""
    if dict_kind(b) != "common":
        return "strong"
    ends = (i + 1 >= len(toks) or bool(_CLAUSE_BREAK_RE.search(seps[i + 1]))
            or _base(toks[i + 1]) in ("and", "or", "&"))
    if ends and (b in _ALSO_NAMES or goal_words.singular(b) in _ALSO_NAMES):
        return "weak"
    return ""


#: Verbs that only a person (or a pet) does, said right after the one who
#: does it: "River needs ...", "Sage keeps losing ...", "Mia and Leo need
#: ...", "liam wants ...". Sentence-start names that are also ordinary
#: words are held this way.
_PERSON_VERBS = frozenset("""needs need needed wants want wanted likes like liked loves love
loved hates hate hated enjoys enjoy enjoyed wishes wish wished keeps keep kept practices
practice practiced learns learn learned struggles struggle struggled forgets forget forgot
remembers remember asks ask asked hopes hope hoped dreams dream prefers prefer misses miss
studies study studied plays play played reads read writes write wrote draws draw drew sings
sing sang dances dance swims swim swam rides ride rode attends attend attended tries try tried
refuses refuse begs beg complains cries cried fights fight fought shares share shared gets get
got has have had goes go went takes take took does do did can can't cannot won't will would
should could must might turns turned turning""".split())
#: Words between the one who does it and the verb ("River really wants").
_PERSON_ADVERBS = frozenset("""just really always never still also only now both all often
usually sometimes finally even already constantly""".split())
#: What may follow "<X> is" for X to be a person: an "-ing" verb is checked
#: on its own; these are the rest ("Sage is obsessed with horses").
_PERSON_IS_NEXT = frozenset("""into obsessed scared afraid terrified crazy learning starting
allergic in at""".split())
#: Words that open a new clause, so the next word may be who does it.
_CLAUSE_LEADS = frozenset("""and but so then when if because since while although though
once until cause cuz plus also""".split())
#: Labels that name people: what is listed after "Kids:" is names.
_PEOPLE_LABELS = frozenset("""kid kids child children grandkid grandkids grandchildren
student students player players member members team family siblings twins guests kiddos
girls boys""".split())
#: Words that stand for people in general, never a name.
_ANYONE = frozenset("""everyone everybody someone somebody anyone anybody nobody noone
people folks each both all none either neither who whoever""".split())
#: Words after which the next word may be the one helped ("help Sage
#: remember", "teach river to swim", "remind Mason to practice").
_HELP_VERBS = frozenset("help helps helping helped teach teaches teaching remind reminds "
                        "reminding let lets".split())


def _person_word(tok: str) -> str:
    """How surely *tok*, said where a person goes (before a person verb),
    is a person's name: ``strong`` (a name by the spelling list, unknown to
    it, or a word it also knows as a name: "Mason", "liam", "Ivy"),
    ``weak`` (an ordinary word: "River", "Sage", "Bear"; held until she
    ticks it, but it stays in the subject when it sits there) or ``""``
    (a joining word, a pronoun, people in general, a relative, a plural
    ordinary word, a time, a thing of a home or a machine)."""
    b = _base(tok)
    if not b or not b.isalpha() or len(b) < 2:
        return ""
    if (b in FUNCTION_WORDS or b in PRONOUNS or b in POSSESSIVES or b in _ANYONE
            or b in goal_words.stopwords() or b in SUBJECT_FILLER or b in BOT_NOUNS
            or b in LANGUAGES or b in _CLAUSE_OPENERS or b in _NEVER_NAMES
            or b in _CLAUSE_LEADS or b in _PERSON_VERBS or b in _PERSON_ADVERBS
            or b in _HELP_VERBS):
        return ""
    forms = _forms(b)
    if forms & (AUDIENCE | BEINGS | RELATIVES | TIME_WORDS | HOUSEHOLD_NOUNS
                | _MACHINE_NOUNS):
        return ""
    if is_hard_word(b) or sensitive_category(b) or is_health_word(b):
        return ""
    kind = dict_kind(b)
    if kind != "common" or b in _ALSO_NAMES or goal_words.singular(b) in _ALSO_NAMES:
        return "strong"
    if forms & _PLANT_ANIMAL:
        return ""          # "Garden needs watering", "Lawn needs mowing"
    if goal_words.singular(b) != b and dict_kind(goal_words.singular(b)) == "common":
        return ""          # "students need", "cats want": people or things in general
    if _verb_like(b):
        return ""          # "started", "plays": what is done, not who does it
    return "weak"


def _person_subject_run(toks: list[str], seps: list[str], i: int) -> list[tuple[int, str]]:
    """The names that start a clause at *i* and do a person's thing:
    "River needs a swim bot" -> [River]; "Mia and Leo need" -> [Mia, Leo];
    "Ezra, Ottilie and Rafe need" -> all three; "Willow and I want" ->
    [Willow]; "liam needs" and (with the shouted sentence lowered first)
    "LIAM NEEDS" -> [liam]. Also the one helped: "help Sage remember",
    "teach river to swim". Returns ``[(index, strength)]``; ``[]`` when the
    words after it are not a person's verb.

    Without this, a child's name that is also an ordinary word, said at the
    start of a sentence or alone, is sent. Used only to HOLD more."""
    n = len(toks)
    if i >= n:
        return []
    prev = _base(toks[i - 1]) if i else ""
    starts_clause = (i == 0 or "\n" in seps[i] or "\r" in seps[i]
                     or bool(_CLAUSE_BREAK_RE.search(seps[i])) or prev in _CLAUSE_LEADS
                     or bool(re.search(r"\s[-–—]\s*$", seps[i])))
    helped = bool(prev) and prev in _HELP_VERBS and not _CLAUSE_BREAK_RE.search(seps[i])
    if not (starts_clause or helped):
        return []
    run: list[tuple[int, str]] = []
    pronoun_in = False
    k = i
    while k < n and len(run) + pronoun_in < 6:
        b = _base(toks[k])
        if k > i and b in ("i", "me") and run:
            pronoun_in = True
        else:
            got = _person_word(toks[k])
            if not got:
                return []
            run.append((k, got))
        nxt = k + 1
        if nxt >= n or _CLAUSE_BREAK_RE.search(seps[nxt]) and "," not in seps[nxt]:
            return []
        if "," in seps[nxt] and nxt + 1 < n:
            if _base(toks[nxt]) in ("and", "&", "or") and not _CLAUSE_BREAK_RE.search(
                    seps[nxt + 1]):
                k = nxt + 1
            else:
                k = nxt
            continue
        if _base(toks[nxt]) in ("and", "&", "or") and nxt + 1 < n \
                and not _CLAUSE_BREAK_RE.search(seps[nxt + 1]):
            k = nxt + 1
            continue
        break
    if not run:
        return []
    v = k + 1
    while v < n and _base(toks[v]) in _PERSON_ADVERBS and not _CLAUSE_BREAK_RE.search(seps[v]):
        v += 1
    if v >= n or _CLAUSE_BREAK_RE.search(seps[v]):
        return []
    verb = _base(toks[v])
    many = len(run) + pronoun_in > 1
    sure = any(s == "strong" for _, s in run)
    ok = False
    if helped:
        # "help Sage remember", "teach river to swim", "help Kai with ...".
        ok = verb in ("to", "with") or verb in _PERSON_VERBS or (
            sure and _dictionary().get(verb) == "common" and _has_verb_forms(verb))
    elif verb in ("is", "was", "'s") and v + 1 < n and not _CLAUSE_BREAK_RE.search(seps[v + 1]):
        after = _base(toks[v + 1])
        ok = (after in _PERSON_IS_NEXT or (after.endswith("ing") and len(after) > 4)
              or _is_count(toks[v + 1]))
    elif verb in _PERSON_VERBS:
        ok = True
    elif ((sure or many) and _verb_like(verb) and not (_forms(verb) & BOT_NOUNS)
          and not (v + 1 < n and _base(toks[v + 1]) in _PERSON_VERBS)):
        # "Mason builds Lego", never "Pokemon cards need ..." (a thing's
        # name, then its plural, then the verb).
        ok = True
    return run if ok else []


def _lower_possessive(toks: list[str], seps: list[str], i: int) -> bool:
    """A name's possessive typed without the apostrophe, in lower case:
    "plan emmas birthday party", "jaydens party".

    The word is not an ordinary word, ends in a single "s", what is left is
    a name or unknown to the spelling list, and a thing word follows in the
    same clause. Tech words of that shape ("postgres database") are held
    too (fail closed; she can tick them)."""
    tok = toks[i]
    b = _base(tok)
    if _is_cap(tok) or len(b) < 4 or not b.endswith("s") or b.endswith("ss")             or _plain_word(b) or dict_kind(b) == "common":
        return False
    stem = b[:-1]
    if dict_kind(stem) == "common" or _plain_word(stem):
        return False
    if i + 1 >= len(toks) or _CLAUSE_BREAK_RE.search(seps[i + 1]):
        return False
    nxt = _base(toks[i + 1])
    return bool(nxt) and nxt.isalpha() and not _plain_word(nxt)


def is_hard_word(word: str) -> str:
    """Why *word* is hard by grammar alone (``""`` when it is not)."""
    w = _base(word)
    if not w:
        return ""
    forms = _forms(w)
    if forms & RELATIVES:
        return "relative"
    if w in PRONOUNS:
        return "pronoun"
    if forms & (MONTHS | WEEKDAYS):
        return "date"
    if w.isdigit() or w in NUMBER_WORDS:
        return "number"
    if _AGE_RE.fullmatch(w):
        return "age"
    # A joined word is hard when any part is: "sister-in-law", "mom's".
    parts = [x for x in re.split(r"[-']", w) if x]
    if len(parts) > 1:
        for part in parts:
            if part in ("s", "m", "ll", "ve", "d", "re"):
                continue
            why = is_hard_word(part)
            if why:
                return why
    return ""


def is_health_word(word: str, *, shape: bool = True) -> bool:
    """A health or diet word (``data_classifier.health_terms`` + the diet list
    + :data:`HEALTH_HELD_WORDS`, and with *shape* a word shaped like a
    medical one, :func:`medical_shape`)."""
    w = _base(word)
    if not w:
        return False
    if _forms(w) & (DIET_WORDS | HEALTH_HELD_WORDS) or (shape and medical_shape(w)):
        return True
    from puffpai.security.data_classifier import health_terms
    # The list knows some conditions only with their "'s" ("Alzheimer's",
    # "Crohn's"); the possessive was taken off above, so try it back on.
    if any(health_terms(form) for form in (w, w + "'s", w + "s")):
        return True
    # The word a listed one grows into ("cancers",
    # "asthmatics") is the same health word.
    return any(health_terms(b) for b in stem_bases(w))


#: Endings taken off a word to find the listed word it grew from
#: ("relapsing", "molesting", "overdosing", "homosexuality", "lesbianism"
#: for the listed "relapse", "molest", "overdose", "homosexual" and
#: "lesbian"). Grammar only, the
#: same for every goal.
_ENDINGS = ("ities", "ity", "isms", "ism", "nesses", "ness", "ingly", "ings", "ing",
            "edly", "ed", "ers", "er", "ists", "ist", "ly", "ies", "es", "s")
#: Endings that make a new word from a whole one with no change of spelling
#: ("homosexual" + "ity", "lesbian" + "ism", "abusive" + "ness", "molest" +
#: "er", "violent" + "ly").
_DERIVING = ("ity", "ism", "ness", "ist", "er", "ly")
#: A base shorter than this is never matched through an ending ("sexist"
#: is not "sex" by this rule, "godly" is not "god"): short listed words
#: match only as typed or by their plural.
_MIN_STEM = 4


def stem_bases(word: str) -> set[str]:
    """The words *word* grew from by one ending.

    For a word the spelling list knows, only what the list itself says:
    the base its plural, past and "-ing" flags made it from ("relapsing":
    "relapse", "gravies": "gravy", never "grave") and a whole word before
    one of :data:`_DERIVING` ("homosexuality": "homosexual", but "gravity"
    is not "grave" + "ity"). For a word the list does not know (slang, a
    typo), every ending is tried with the usual spelling changes
    ("vapeing": "vape", "bpds": "bpd" is too short and matches as a plural
    instead). Only bases
    of at least :data:`_MIN_STEM` letters. Used to HOLD more, never to let
    a word out."""
    w = _base(word)
    if not w:
        return set()
    d = _dictionary()
    out: set[str] = set()
    if d.get(w) == "common":
        # The list's own flags are sure ("sexed" is "sex" + "ed"): any length.
        out |= {b for b in _INFLECTED_FROM.get(w, set()) if b != w}
        for end in _DERIVING:
            if w.endswith(end) and len(w) - len(end) >= _MIN_STEM:
                out.add(w[:-len(end)])
        return out
    for end in _ENDINGS:
        if not w.endswith(end) or len(w) - len(end) < _MIN_STEM - 1:
            continue
        b = w[:-len(end)]
        cands = {b}
        if not b.endswith("e"):
            cands.add(b + "e")
        if b.endswith("i"):
            cands.add(b[:-1] + "y")
        if len(b) > 3 and b[-1] == b[-2] and b[-1] not in "aeioulsz":
            cands.add(b[:-1])
        out |= {c for c in cands if len(c) >= _MIN_STEM and c != w}
    return out


#: Endings that only medical words have (word lists alone miss
#: "mastectomy", "biopsy", "prognosis"): operations
#: ("-ectomy", "-otomy", "-ostomy", "-oscopy", "-plasty"), conditions
#: ("-itis", "-osis", "-oma", "-emia", "-algia", "-opathy", "-phobia") and
#: specialties ("-iatric", "-iatry"). Word shape only, the same for every goal.
_MEDICAL_ENDINGS = ("ectomy", "ectomies", "otomy", "otomies", "ostomy", "ostomies",
                    "oscopy", "oscopies", "plasty", "plasties", "itis", "osis",
                    "oma", "omas", "omata", "emia", "aemia", "emias", "algia", "opathy",
                    "opathies", "phobia", "phobias", "iatric", "iatrics", "iatry",
                    "iatrist", "iatrists",
                    # "psoriasis", "dysplasia",
                    # "vulvodynia", "narcolepsy", "diarrhea", "hematuria".
                    "iasis", "iases", "plasia", "dynia", "lepsy", "rrhea", "rrhoea",
                    "aturia", "orrhage", "orrhages")
#: Everyday words with one of those endings.
_MEDICAL_SHAPE_ORDINARY = frozenset("""dichotomy dichotomies nostalgia academia bohemia
diploma diplomas aroma aromas chroma stroma sonoma tacoma oklahoma paloma idioma soma coma
osmosis symbiosis metamorphosis apotheosis hypnosis diagnosis diagnoses microscopy
spectroscopy stereoscopy telescopy kaleidoscopy meiosis mitosis apoptosis wapitis
diplomata""".split())


#: Endings of medicine names (the shared naming stems: "-azine", "-azepam",
#: "-oxetine", "-statin", "-mab", "-navir", ...). Read only on words the
#: spelling list does not know ("xylazine", "promethazine"), never on an
#: everyday word ("magazine").
_DRUG_ENDINGS = ("azine", "azepam", "azolam", "oxetine", "triptyline", "pramine",
                 "prazole", "sartan", "olol", "statin", "mab", "navir", "cillin", "mycin",
                 "cycline", "floxacin", "tidine", "glutide", "gliflozin", "gliptin",
                 "dronate", "barbital", "codone", "morphone", "fentanil", "azodone",
                 "apine", "idone", "afil", "setron", "lukast", "tadine", "profen")


def medical_shape(word: str) -> bool:
    """Is *word* shaped like a medical word (an operation, a condition, a
    specialty, or a medicine name the spelling list does not know)? Only
    words of seven letters or more, and never one of
    :data:`_MEDICAL_SHAPE_ORDINARY`. Used to HOLD more, never to let a word
    out."""
    w = _base(word)
    if len(w) < 7 or not w.isalpha() or w in _MEDICAL_SHAPE_ORDINARY:
        return False
    if w.endswith(_DRUG_ENDINGS) and dict_kind(w) == "unknown":
        return True
    if w.endswith("oses"):
        # The plural of an "-osis" word ("prognoses"), never a verb that
        # only ends the same way ("chooses", "purposes").
        one = w[:-2] + "is"
        return one not in _MEDICAL_SHAPE_ORDINARY and dict_kind(one) == "common"
    return w.endswith(_MEDICAL_ENDINGS)


#: Endings that make a noun from a verb or an adjective ("miscarry" +
#: "age", "addict" + "ion"): tried after an inflection is taken off, so a
#: verb form finds its listed noun ("miscarried": "miscarriage").
_NOUN_ENDINGS = ("age", "ation", "ion", "ment", "ance", "ence")
#: Listed nouns whose verb is an everyday word ("garnishing" a cake,
#: "naturalizing" bulbs, "communing" with nature): never reached this way.
_DERIVED_ORDINARY = frozenset("garnishment naturalization communion".split())


def _derived_listed(w: str) -> str:
    """The category of the listed noun *w* is a verb form of ("miscarried"
    for "miscarriage", "addicting" for "addiction"), ``""`` when none. Roots
    shorter than five letters are never tried."""
    roots: set[str] = set()
    # Verb forms only (past and "-ing"): a plural is a noun already.
    for end, back in (("ied", "y"), ("ying", "y"), ("ed", ""), ("ed", "e"),
                      ("ing", ""), ("ing", "e")):
        if w.endswith(end) and len(w) - len(end) >= 5:
            roots.add(w[:-len(end)] + back)
    for r in roots:
        stems = {r, r[:-1] + "i" if r.endswith("y") else r, r[:-1] if r.endswith("e") else r}
        for st in stems:
            if len(st) < 5:
                continue
            for end in _NOUN_ENDINGS:
                cand = st + end
                # A listed word with an everyday meaning too never reaches
                # its verb ("visits" is not "visitation").
                if (cand != w and cand in SENSITIVE_WORDS and cand not in STORY_EXEMPT
                        and cand not in _DERIVED_ORDINARY):
                    return SENSITIVE_WORDS[cand]
    return ""


def listed_lookup(word: str, *, glued: bool = True) -> str:
    """The category of one plain word by this module's own lists: as typed,
    its singular, the listed word it grew from by one ending
    (:func:`stem_bases`), or the listed noun it is a verb form of
    (:func:`_derived_listed`). ``""`` when none. *glued*: also a listed word
    glued to another (:func:`_glued_listed`)."""
    w = _base(word)
    if not w:
        return ""
    for f in _forms(w) | {w}:
        if f in SENSITIVE_WORDS:
            return SENSITIVE_WORDS[f]
    for b in sorted(stem_bases(w)):
        for f in _forms(b) | {b}:
            if f in SENSITIVE_WORDS:
                return SENSITIVE_WORDS[f]
    if not w.isalpha():
        return ""
    return _derived_listed(w) or (_glued_listed(w) if glued else "")


#: Listed words that also start many unrelated made-up words ("transcoder",
#: "godmode"): never read as the first half of a glued word.
_GLUE_PREFIX_ORDINARY = frozenset("trans god".split())


def _glued_listed(w: str) -> str:
    """The category of a word the spelling list does not know that is a
    listed sensitive word glued to an ordinary or listed one ("hivpositive",
    "cancerfree", "rehabcenter"). Both
    halves have three letters or more. ``""`` when none."""
    if len(w) < 6 or dict_kind(w) != "unknown":
        return ""
    # A short prefix on a long listed word ("peri" + "menopause", "post" +
    # "partum", "anti" + "psychotic").
    for k in range(2, 6):
        right = w[k:]
        if len(right) >= 6 and right in SENSITIVE_WORDS and right not in STORY_EXEMPT:
            return SENSITIVE_WORDS[right]
    for k in range(3, len(w) - 2):
        left, right = w[:k], w[k:]
        cat = SENSITIVE_WORDS.get(left) or (
            "health" if is_health_word(left, shape=False) else "")
        if cat and left not in _GLUE_PREFIX_ORDINARY and left not in STORY_EXEMPT \
                and left not in _NO_GLUE \
                and (dict_kind(right) == "common" or right in SENSITIVE_WORDS):
            return cat
        cat = SENSITIVE_WORDS.get(right) or (
            "health" if is_health_word(right, shape=False) else "")
        if cat and right not in STORY_EXEMPT and right not in _NO_GLUE \
                and dict_kind(left) == "common" \
                and left not in FUNCTION_WORDS:
            return cat
    return ""


def sensitive_category(word: str, prev: str = "", *, shape: bool = True) -> str:
    """The sensitive category of *word* (``""`` when it has none).

    ``health`` (the data classifier's health list, the diet list, mental
    health, addiction, sexual health), ``sex``, ``belief``, ``legal``,
    ``money``, ``violence`` or ``grief``. *prev* is the word before it, for
    the one word that is ordinary in a common pairing ("study aids").
    """
    w = _base(word)
    if not w:
        return ""
    if is_health_word(w, shape=shape):
        return "health"
    if _ordinary_here(w, prev):
        return ""
    cat = listed_lookup(w)
    if cat:
        return cat
    # A joined word is sensitive when a part is ("ex-convict", "anti-anxiety"),
    # or when it is one listed word written with a hyphen ("non-binary",
    # "self-harm").
    parts = [x for x in re.split(r"[-']", w) if len(x) > 1]
    if len(parts) > 1:
        for part in parts:
            cat = sensitive_category(part)
            if cat:
                return cat
    if "-" in w and w.replace("-", "") != w:
        return sensitive_category(w.replace("-", ""))
    return ""


def sensitive_why(word: str, prev: str = "") -> str:
    """``health`` or ``private`` for a sensitive word, ``""`` otherwise."""
    cat = sensitive_category(word, prev)
    if not cat:
        return ""
    return "health" if cat == "health" else "private"


#: Words between a thing and "coming out" ("my prints keep coming out").
_COMING_AUX = frozenset("""keep keeps kept is are was were be been being still always all
just also often sometimes usually never""".split())


def _coming_out_of_hers(low: list[str]) -> bool:
    """Is some "coming out" in *low* a person's (identity), not a thing's?
    "my prints keep coming out stringy" and "the bread is coming out flat"
    are things: an ordinary noun that is not a person sits right before it
    (past "keep", "is", ...). Fail closed: anything else counts ("my coming
    out", "I'm coming out to my parents", "coming out" alone). Without the
    noun check, a 3D printer goal's "coming out" holds every word of the
    goal as a private story."""
    for i in range(len(low) - 1):
        if (low[i], low[i + 1]) != ("coming", "out"):
            continue
        k = i - 1
        while k >= 0 and low[k] in _COMING_AUX:
            k -= 1
        if k < 0:
            return True
        who = low[k]
        if (not who.isalpha() or who in PRONOUNS or who in POSSESSIVES or who in FUNCTION_WORDS
                or _kin(who) or _forms(who) & (AUDIENCE | BEINGS | _HUMAN_BEINGS)
                or dict_kind(who) != "common" or who in _ALSO_NAMES):
            return True
    return False


def sensitive_phrase_hits(words: list[str]) -> list[tuple[tuple[str, ...], str]]:
    """The sensitive word pairs in *words* ("passed away", "mental health"),
    and two neighbours that are one sensitive word split in two ("non
    binary", "self harm": "non-binary" reads as "non binary" once the
    hyphen is gone)."""
    hits = [(seq, cat) for seq, cat in SENSITIVE_PHRASES if contains_seq(words, seq)]
    low = [_base(w) for w in words]
    if (("coming", "out"), "sex") in hits and not _coming_out_of_hers(low):
        hits.remove((("coming", "out"), "sex"))
    # A habit word that is hers and ends the thought ("because of my
    # cutting", "my using and"), never one a thing follows ("my cutting
    # board"). See OWNED_HABITS.
    stop = goal_words.stopwords()
    for i in range(1, len(low)):
        if low[i] != "fired" or low[i - 1] not in _FIRED_AFTER:
            continue
        who = low[i - 2] if i >= 2 else ""
        if who in PRONOUNS or _kin(who) or (i + 1 < len(low) and low[i + 1] == "from"):
            seq = (low[i - 1], "fired")
            if (seq, "money") not in hits:
                hits.append((seq, "money"))
    for i in range(len(low) - 1):
        cat = OWNED_HABITS.get(low[i + 1])
        if not cat or not _owner_before(low, i + 1):
            continue
        nxt = low[i + 2] if i + 2 < len(low) else ""
        # "my ms flare", "her lithium dose", "my wife's lithium blood tests".
        if not nxt or nxt in FUNCTION_WORDS or nxt in stop or nxt in PRONOUNS \
                or nxt in POSSESSIVES or nxt in _HEALTH_HEADS or nxt in _FLARE_WORDS \
                or nxt in _HABIT_NEXT:
            seq = (low[i], low[i + 1])
            if (seq, cat) not in hits:
                hits.append((seq, cat))
    # A numbered private thing when it is hers ("my chapter 7", "her stage
    # 4", "has stage 4", "we are on section 8"),
    # never a book's chapter or a play's stage.
    for i in range(len(low) - 1):
        numbers, cat = _NUMBERED_PRIVATE.get(low[i], ((), ""))
        if cat and low[i + 1] in numbers and _owner_before(low, i, numbered=True):
            seq = (low[i], low[i + 1])
            if (seq, cat) not in hits:
                hits.append((seq, cat))
    for i in range(len(low) - 1):
        a, b = low[i], low[i + 1]
        if not a or not b or not (a + b).isalpha():
            continue
        if sensitive_category(a) or sensitive_category(b):
            continue            # already held word by word
        if a in FUNCTION_WORDS or b in FUNCTION_WORDS or a in PRONOUNS or b in PRONOUNS:
            continue            # "the" + "osmosis" is not one word
        # Only a listed word split in two, never a word shape: "the" +
        # "osmosis" reads like "-osis". Never a glued pair
        # either ("me" + "through" is not "meth" + "rough", so "through" is
        # not held as health): a glued listed word has a listed half, held on its own.
        ab = a + b
        cat = ("health" if is_health_word(ab, shape=False) else "") \
            or ("" if _ordinary_here(ab, "") else listed_lookup(ab, glued=False))
        if cat and ((a, b), cat) not in hits:
            hits.append(((a, b), cat))
    return hits


#: Words after a habit word that make it a condition ("my ms flare").
_FLARE_WORDS = frozenset("flare flares flareup flareups relapse relapses".split())
#: Words after a habit word that make it a body test ("her lithium blood
#: tests", "my lithium levels").
_HABIT_NEXT = frozenset("""blood bloodwork level levels lab labs test tests result results
check checks checkup checkups draw draws monitoring""".split())
#: A number after these is a private thing when it is hers: bankruptcy
#: ("chapter 7"), a cancer's stage ("stage 4"), housing help ("section 8").
_NUMBERED_PRIVATE: dict[str, tuple[frozenset[str], str]] = {
    "chapter": (frozenset("7 11 13 seven eleven thirteen".split()), "money"),
    "stage": (frozenset("3 4 iii iv three four".split()), "health"),
    "section": (frozenset("8 eight".split()), "money"),
}
#: Verbs in front of a numbered private thing, per word ("filed chapter 7",
#: "has stage 4", "on section 8"). Never "on chapter 7": a book club.
_NUMBERED_VERBS: dict[str, frozenset[str]] = {
    "chapter": frozenset("filed filing file files declared declaring".split()),
    "stage": frozenset("has have had".split()),
    "section": frozenset("on".split()),
}


def _owner_before(low: list[str], i: int, *, numbered: bool = False) -> bool:
    """Is the word at *i* hers or one of her people's: a my/our in front,
    a relative's possessive ("daughter" + "s"), or "<relative> has" / "he
    has"? With *numbered*, also "filed", "on"."""
    if i < 1:
        return False
    p = low[i - 1]
    if p in POSSESSIVES:
        return True
    if p == "s" and i >= 2 and (_kin(low[i - 2]) or _forms(low[i - 2]) & (AUDIENCE | BEINGS)):
        return True
    if p in ("has", "had", "have") and i >= 2 and (
            _kin(low[i - 2]) or low[i - 2] in ("he", "she")):
        return True
    return numbered and p in _NUMBERED_VERBS.get(low[i], ())


def loose_forms(word: str) -> set[str]:
    """*word*'s forms for matching against the goal's own personal list.

    Wider than :func:`_forms`: a plural or possessive typed without the
    apostrophe ("emmas", "annas", "sofias") also counts as the name, because
    :func:`goal_words.singular` rightly leaves "as" words alone for the
    dictionary. Only ever used to HOLD more, never to let a word out.
    """
    w = _base(word)
    out = _forms(w)
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        out.add(w[:-1])
        if len(w) > 4 and w.endswith("es"):
            out.add(w[:-2])
    # Typed-out endings on a name
    # ("emmass", "jakess", "annases") also count as the name.
    if len(w) > 5 and w.endswith("ses"):
        out.add(w[:-3])
    if len(w) > 4 and w.endswith("ss"):
        out.add(w[:-2])
    # A loose form never turns a word into a pronoun ("themes" is not
    # "them"): pronouns are matched only as typed.
    return {f for f in out if f == w or f not in PRONOUNS} - {""}


def word_keys(word: str) -> set[str]:
    """Every form of a held word to match on: :func:`loose_forms` of the word
    and of each part of a joined word ("rose-marie" also holds "rose" and
    "marie", "jean-luc" holds "jean" and "luc")."""
    w = _base(word)
    out = loose_forms(w)
    # A one-letter part must not stop the split ("o'brien" would leave
    # "brien" sendable): every part of two letters or
    # more, and the parts joined ("obrien", "rosemarie").
    split = [x for x in re.split(r"[-'\s]+", w) if x]
    if len(split) > 1:
        for part in split:
            if len(part) > 1:
                out |= loose_forms(part)
        out |= loose_forms("".join(split))
    return out


def home_words(home: dict | None) -> tuple[set[str], list[tuple[str, ...]]]:
    """Her home town and state as (single words, word sequences).

    A one-word town or state is held as that word. A longer one is held as
    any two or more of its words in order ("salt lake"), and each of its
    words that is not an ordinary dictionary word ("mexico").
    Two-letter state codes that are joining words ("in", "or", "me") are not
    held on their own; the full name still is. A state given as its postal
    code ("OH") also holds its name ("ohio"), and a name also holds its code.
    Each comma part of the town, state and label counts ("Columbus, OH").
    """
    singles: set[str] = set()
    seqs: list[tuple[str, ...]] = []
    if not isinstance(home, dict):
        return singles, seqs
    names: list[str] = []
    for key in ("town", "state", "label"):
        if home.get(key):
            names.extend(str(home.get(key))[:200].split(",")[:3])
    for name in list(names):
        key = " ".join(goal_words.raw_words(name))
        if key in STATE_CODES:
            names.append(STATE_CODES[key])
        elif key in _STATE_NAMES:
            names.append(_STATE_NAMES[key])
    for name in names:
        words = [w for w in goal_words.raw_words(name[:120]) if w]
        if not words:
            continue
        if len(words) == 1:
            w = words[0]
            # A short one (a postal code such as "TX": the word list carries
            # every code as an acronym) is held unless it is a joining word
            # ("in", "or", "on", "me"); the full state name is held anyway.
            if len(w) > 2 or w not in goal_words.stopwords():
                singles |= _forms(w)
            continue
        for i in range(len(words)):
            for j in range(i + 2, len(words) + 1):
                seqs.append(tuple(words[i:j]))
        for w in words:
            if dict_kind(w) != "common" and len(w) > 2:
                singles |= _forms(w)
    return singles, seqs


def contains_seq(words: list[str], seq: tuple[str, ...]) -> bool:
    n = len(seq)
    folded = [goal_words.singular(w) for w in words]
    target = tuple(goal_words.singular(w) for w in seq)
    return any(tuple(folded[i:i + n]) == target for i in range(len(words) - n + 1))


# --- the goal's personal words -----------------------------------------------------

@dataclass
class PersonalWords:
    hard: set[str] = field(default_factory=set)
    held: list[dict] = field(default_factory=list)
    subject: str = ""
    subject_notes: list[str] = field(default_factory=list)
    #: Held word forms that are a time of her plans (seasons, holidays,
    #: weekends). Sent only as a word of the subject.
    dates: set[str] = field(default_factory=set)
    #: Days of the year of more than one word that her goal names ("memorial
    #: day"): no search phrase may hold one outside the subject.
    date_seqs: list[tuple[str, ...]] = field(default_factory=list)
    #: Held word -> every piece of it as typed ("DeShawn": "shawn",
    #: "O'Brien": "brien" and "obrien"), so no part of a name is sent.
    pieces: dict[str, set[str]] = field(default_factory=dict)
    #: The goal names a time (a month, a weekday, a season, a holiday, a day
    #: of the year) of her own plans. Then every word of that kind is held in
    #: every phrase that leaves, including a model's other word for it
    #: ("autumn" for her "fall").
    plan_time: bool = False
    #: Word forms that count as the subject's own besides its words: a money
    #: word said as her own job in a money bot ("pay off credit card debt"
    #: in "a family budget bot"). See :func:`subject_domain_words`.
    subject_also: set[str] = field(default_factory=set)
    #: Word forms held though she did not type them ("estate" in a goal that
    #: says "my late aunt"). Never listed or ticked.
    quiet: set[str] = field(default_factory=set)
    #: The subject words that are words of her own jobs, her own health
    #: topic or the model of her machine: searched,
    #: so a phrase builder must not drop them as personal ("study name
    #: flashcard" for "drug name flashcards").
    subject_job: set[str] = field(default_factory=set)


class _Marks:
    """Collects hard and held words (every form: as typed and singular)."""

    def __init__(self) -> None:
        self.hard: dict[str, str] = {}
        self.held: dict[str, tuple[str, bool]] = {}
        #: Held words as typed, in order, for the owner's list.
        self.typed: list[str] = []
        #: Offsets in the goal of words a cue marks as a name for sure; the
        #: subject never keeps one.
        self.strong: set[int] = set()
        #: Forms of words she used as a person's name somewhere in the goal
        #: ("my son River", "River needs ...", "named Biscuit", "I'm Leo"):
        #: held everywhere, the subject too.
        self.person: set[str] = set()
        #: Word forms held although she did not type them (what a model
        #: would say about a detail she gave: "estate" after "my late
        #: aunt"). Never listed, never tickable.
        self.quiet: set[str] = set()
        #: Held forms that are a time of her plans (seasons, holidays).
        self.dates: set[str] = set()
        #: Days of the year of more than one word named in the goal.
        self.date_seqs: list[tuple[str, ...]] = []
        #: Held word (lower case) -> its pieces as typed (camel case,
        #: apostrophes, hyphens) and the pieces joined.
        self.pieces: dict[str, set[str]] = {}

    def plan_time(self) -> bool:
        """Does the goal name a time of her plans (see PersonalWords)?"""
        return bool(self.dates or self.date_seqs) or any(
            why == "date" for why in self.hard.values())

    def mark_hard(self, word: str, why: str) -> None:
        for f in _forms(word):
            self.hard.setdefault(f, why)
            self.held.pop(f, None)

    def mark_date(self, word: str) -> None:
        """A time of her plans: held, never tickable, and remembered as a
        date. Held rather than hard, so a job named after it ("plan for
        Christmas") is still searched by its other words."""
        self.mark_held(word, "date", False)
        self.dates |= _forms(word) - set(self.hard)

    def mark_held(self, word: str, why: str, can_allow: bool) -> None:
        typed = _base(word)
        if typed and typed not in self.typed:
            self.typed.append(typed)
        if typed:
            got = name_pieces(word)
            if got:
                self.pieces.setdefault(typed, set()).update(got)
        for f in _forms(word):
            if f in self.hard:
                continue
            old = self.held.get(f)
            # A word that can never be allowed stays that way.
            if old is None or (old[1] and not can_allow):
                self.held[f] = (why, can_allow)


def glued_to(word: str, keys: Iterable[str]) -> bool:
    """Is *word* one of *keys* (held names) with more glued on
    ("emmasbirthday", "graceis") or the start of one cut short ("zo" of
    "zoe")? Never an ordinary word of the spelling list ("leopard" for
    "leo", "an" for "anna"). Used only to HOLD more."""
    w = _base(word)
    if not w or not w.isalpha() or is_common_word(w):
        return False
    for k in keys:
        if len(k) < 3 or k == w:
            continue
        if len(w) > len(k) and (w.startswith(k) or w.endswith(k)):
            return True
        if 2 <= len(w) < len(k) and k.startswith(w):
            return True
        # The end of one cut off ("eigh" of "kayleigh", "shawn" of
        # "deshawn"; a model may split "Kayłeigh" into "kay eigh" and
        # send "eigh").
        if 3 <= len(w) < len(k) and k.endswith(w):
            return True
    return False


def name_pieces(word: str) -> set[str]:
    """The pieces of a word as typed that a search could split it into:
    camel case ("DeShawn": "shawn"), apostrophes ("O'Brien": "brien") and
    hyphens ("Rose-Marie": "rose", "marie"), each with two letters or
    more, and all its pieces joined ("obrien", "rosemarie"). Empty for a
    plain one-piece word. Used only to HOLD more (so "brien", "angelo" and
    "shawn" are never search words)."""
    text = str(word or "").replace("’", "'")
    raw = goal_words.raw_words(text)
    # The possessive ("Jake's") is not a piece.
    if len(raw) > 1 and raw[-1] == "s" and text.lower().endswith("'s"):
        raw = raw[:-1]
    if len(raw) < 2:
        return set()
    out = {p for p in raw if len(p) >= 2}
    out.add("".join(raw))
    return out - {""}


#: Words before "fired" when a person lost a job ("I got fired", "my husband
#: was fired from"), never a kiln ("the pieces get fired twice").
_FIRED_AFTER = frozenset("got get gets was were been getting be".split())

#: Joining words that are part of an idiom when they come second ("coming
#: out", "laid off").
_PARTICLES = frozenset("out off up".split())


#: Without this, "spring recess", "fall 2026 kitchen remodel" and
#: "fall-2026" send the season. "spring" and "fall" as typed
#: are a time of year unless the words around them say they are a verb or a
#: thing: the words after that make them one ("fall asleep", "spring rolls",
#: "fall detection", "Spring Boot") ...
_SEASON_THING_AFTER = frozenset("""asleep behind apart off down out back through into short
ill sick silent prey flat open up away roll rolls onion onions water waters mattress
mattresses loaded load constant constants boot framework scale scales clip clips steel coil
coils chicken detection detector detectors risk risks prevention alert alerts damage
protection hazard hazards cleaning clean guys guy of clamp clamps hinge hinges pin pins
washer washers""".split())
#: ... and the words before ("to fall", "we fall", "hot spring", "coil
#: spring", "free fall").
_SEASON_THING_BEFORE = frozenset("""to will would could can might may must should shall don't
dont doesn't doesnt didn't didnt won't wont not never i we you they he she it hot coil leaf
box mineral natural thermal compression extension torsion free rock snow""".split())


#: What a glued time word is made of ("springbreak", "falltrip",
#: "xmastrip", "julytrip", "maytrip", "marchbreak", "newyearsparty"). The
#: time itself ...
_GLUE_CORE = frozenset(SEASONS | PLAIN_SEASONS | HOLIDAYS | WEEKDAYS | frozenset(
    """january february march april may june july august september october november
    december jan feb mar apr jun jul aug sep sept oct nov dec newyear newyears xmas yule
    weekend nye""".split()))
#: ... and what may be glued to it: plan words, stretches of time, more time.
_GLUE_REST = frozenset(PLAN_NOUNS | PERIOD_NOUNS | TRAVEL_NOUNS | frozenset(
    """new year years eve s party parties day days trip trips time plans plan fun
    shopping packing""".split()))


def glued_time(word: str) -> bool:
    """Is *word* a time glued to plan words, and nothing else ("springbreak",
    "falltrip", "xmastrip", "julytrip", "marchbreak", "newyearsparty")? The
    whole word must split into such pieces with at least one time among
    them, and the spelling list must not know it ("fallout", "mayday" and
    "springboot" are not). Used only to HOLD more."""
    w = _base(word)
    if len(w) < 5 or not w.isalpha() or is_common_word(w):
        return False
    return bool(glued_time_pieces(w))


def glued_time_pieces(w: str) -> list[str]:
    """The pieces of a glued time word ("springbreaktrip": ["spring",
    "break", "trip"]), or [] when it is not one. Longest pieces first."""
    n = len(w)
    # best[i]: pieces for w[:i] and whether a time is among them.
    best: list[Optional[tuple[list[str], bool]]] = [None] * (n + 1)
    best[0] = ([], False)
    for i in range(n):
        if best[i] is None:
            continue
        got, has_core = best[i]  # type: ignore[misc]
        for j in range(n, i, -1):
            piece = w[i:j]
            core = piece in _GLUE_CORE
            if not core and piece not in _GLUE_REST:
                continue
            cand = (got + [piece], has_core or core)
            old = best[j]
            if old is None or (cand[1] and not old[1]):
                best[j] = cand
    end = best[n]
    if end is None or not end[1] or len(end[0]) < 2:
        return []
    return end[0]


def _is_plan_time(base: str, prev: str, nxt: str, prev2: str = "", nxt2: str = "", *,
                  default: bool = True) -> bool:
    """Is *base* a time of her plans? A season that is
    only a season ("summer"), a holiday or a weekend. "spring" and "fall"
    after a cue ("next spring", "in the fall"; *prev2* is the word before
    *prev*), before a plan word ("a fall wedding", "spring recess") or a
    year ("fall 2026"), and otherwise too unless the words around them make
    them a verb or a thing ("fall asleep", "spring rolls", "the fall of
    Rome"; fail closed, a season is the default).
    *default* False leaves out that last rule (the lower-case word-by-word
    pass, which cannot see a product's capitals: "Spring Boot")."""
    forms = _forms(base)
    if forms & HOLIDAYS:
        return True
    if forms & PLAIN_SEASONS:
        # "A winter scarf", "autumn leaf peeping spots": a season right before a
        # thing names a kind of thing, not when she does something. After a
        # cue ("this winter", "in the summer"), before a plan word ("summer
        # trip", "winter break", "summer plans") or a year, or with nothing
        # after it, it is a time of her plans.
        p, n = _base(prev), _base(nxt)
        if p in SEASON_CUES or (p == "the" and _base(prev2) in _THE_CUES):
            return True
        # Hers ("my summer classes", "our winter plans", "my son's summer
        # league"): the time of her plans whatever follows (fail closed).
        raw_prev = str(prev or "").lower().replace("’", "'")
        if p in POSSESSIVES or raw_prev.endswith("'s") or p == "s":
            return True
        return not _names_a_thing(n)
    if forms & SEASONS:
        p, n = _base(prev), _base(nxt)
        # "after the fall", "before the fall", "since the fall": the event
        # (a crash, the fall of something), not the season.
        if p == "the" and _base(prev2) in ("after", "before", "since") and "fall" in forms:
            return False
        if (p in SEASON_CUES or (p == "the" and _base(prev2) in _THE_CUES)
                or bool(_forms(n) & PLAN_NOUNS) or n.isdigit()
                or (n == "of" and _base(nxt2).isdigit())):
            return True
        # Only the word as typed ("spring", "fall"): "falls" and "springs"
        # are a waterfall, a coil or a verb, and time only after a cue.
        if base not in SEASONS or not default:
            return False
        # "a fall frost", "the spring jacket": an article, then the season
        # naming a kind of thing, not a plan (a plan word is caught above).
        if p in ("a", "an", "the") and n and n.isalpha() and n not in FUNCTION_WORDS                 and not (_forms(n) & PERIOD_NOUNS):
            return False
        return not (p in _SEASON_THING_BEFORE or n in _SEASON_THING_AFTER)
    return False


#: Cues that make "the <season>" a time ("in the fall", "during the
#: summer"). Not "after/before/since the fall": that is an event.
_THE_CUES = SEASON_CUES - frozenset("after before since around".split())


def _names_a_thing(n: str) -> bool:
    """Is *n*, the word after a season, a thing the season describes ("a
    winter scarf", "autumn leaf")? An ordinary word of the spelling list that
    is not a plan or time word, a joining word, a digit or a verb form."""
    if not n or not n.isalpha() or n in FUNCTION_WORDS or n in goal_words.stopwords():
        return False
    f = _forms(n)
    if f & (PLAN_NOUNS | PERIOD_NOUNS | TRAVEL_NOUNS | PLAN_AFTER | TIME_WORDS):
        return False
    if f & (RELATIVES | AUDIENCE) or n in PRONOUNS or n in _TIME_ADVERBS:
        return False
    return dict_kind(n) == "common"


#: Joining words between two seasons ("summer or fall", "spring and summer",
#: "summer/fall" once the slash is gone).
_SEASON_JOINS = frozenset("and or to through into thru".split())


def _raw_time_words(raw: list[str]) -> set[int]:
    """Indexes in *raw* (the goal's raw words) that are a time of her plans,
    read word by word, so a
    hyphen, a slash or digits no longer hide one ("summer-trip",
    "summer/fall", "summer2026"). Also "spring" or "fall" next to another
    season ("summer or fall"), a season or holiday glued into a word the
    spelling list does not know ("christmasbreak"), and a word that names a
    stretch of time right after one ("summer vacation", "Christmas break",
    "holiday season", "summer-time")."""
    out: set[int] = set()

    def plain_time(w: str) -> bool:
        return bool(_forms(w) & (PLAIN_SEASONS | HOLIDAYS))

    for k, w in enumerate(raw):
        prev = raw[k - 1] if k else ""
        prev2 = raw[k - 2] if k > 1 else ""
        nxt = raw[k + 1] if k + 1 < len(raw) else ""
        nxt2 = raw[k + 2] if k + 2 < len(raw) else ""
        if _is_plan_time(w, prev, nxt, prev2, nxt2, default=False):
            out.add(k)
            continue
        letters = re.findall(r"[a-z]+", w)
        if len(letters) > 1 or (letters and letters[0] != w):
            # "summer2026": the letters beside the digits.
            if any(plain_time(x) or _forms(x) & SEASONS for x in letters):
                out.add(k)
                continue
        if w.isalpha() and dict_kind(w) != "common" and any(
                len(w) > len(t) and (w.startswith(t) or w.endswith(t)) for t in _GLUE_TIMES):
            out.add(k)
            continue
        if glued_time(w):
            out.add(k)
            continue
        if _forms(w) & SEASONS:
            near = [x for x in (raw[k - 2] if k > 1 and prev in _SEASON_JOINS else prev,
                                raw[k + 2] if k + 2 < len(raw) and nxt in _SEASON_JOINS
                                else nxt) if x]
            if any(plain_time(x) or (_forms(x) & SEASONS and x != w) for x in near):
                out.add(k)
    # A stretch of time named after one ("summer vacation", "Christmas
    # Eve"), skipping the "s" of "Valentine's".
    for k, w in enumerate(raw):
        # A trip at that time too ("summer trip", "Xmas getaway").
        if k in out or not (_forms(w) & (PERIOD_NOUNS | TRAVEL_NOUNS)):
            continue
        j = k - 1
        if j >= 0 and raw[j] == "s":
            j -= 1
        if j >= 0 and j in out:
            out.add(k)
    return out


# --- what she or her people are going through (grammar, not a list) ----------------
#
# "my wife deals with psoriasis", "since my hallucinations and paranoia got
# worse", "my kid with sickle cell" must not send the condition. No
# word list can name every condition, drug or trouble, so the sentence shape
# says it instead: what follows one of these shapes is her private story and
# is held (never tickable), whatever the word is. Grammar only, the same for
# every goal.

#: "<person> deals with X", "we are sorting out X", "he suffers from X".
#: The particle, then the verbs in front of it.
_STORY_FRAMES: dict[str, frozenset[str]] = {
    # Not "struggles with": a child who struggles with fractions needs a
    # fractions bot, and that word is the search.
    "with": frozenset("""deal deals dealing dealt cope copes coping coped live lives living
lived diagnosed suffer suffers suffering suffered battle battles battling battled""".split()),
    "from": frozenset("""suffer suffers suffering suffered recover recovers recovering
recovered""".split()),
    "out": frozenset("sort sorts sorting sorted".split()),
    "through": frozenset("going went gone getting got working worked".split()),
    "for": frozenset("treated".split()),
}
#: Frames that tell a story whoever says them ("diagnosed with X",
#: "recovering from X"): no person needed in front.
_STORY_ANY = frozenset("diagnosed suffering suffers suffered recovering treated".split())
#: Between the person and the verb: "my son has been struggling with".
_STORY_AUX = frozenset("""am is are was were be been being has have had still also really
always constantly now currently recently just both all often sometimes lately finally slowly
kind sort of ve s m re""".split())
#: Who can be going through something (besides relatives and her own people).
_STORY_PEOPLE = frozenset("""i we he she they i'm im we're he's she's they're i've we've
who""".split())
#: Words that open a reason or a time in her life: "since my X started",
#: "because of my X", "due to my X".
_STORY_OPENERS = frozenset("since because cause cuz due".split())
#: What ends "since my X ..." ("started", "got worse", "is back").
_EVENT_VERBS = frozenset("""started starts start began begun begin begins got gets get came
comes come went kicked flared returned showed hit hits happened worsened turned took is was
are were has have had keeps kept keep continues continued stopped broke died makes made
means needs need wants want can could will would should does did doesn't don't won't isn't
wasn't""".split())
#: What makes "since my X" a story and not a plain reason: X began, came
#: back or got worse ("since my ibs started", "since my hallucinations got
#: worse"), never "because my tomatoes keep dying".
_ONSET_VERBS = frozenset("""started starts start began begun begin begins got gets came comes
kicked flared flares returned showed hit happened worsened""".split())
#: A thing sold for the body, whose word in front is the condition or the
#: medicine when it is hers ("my thyroid pills", "her insulin pump" is a
#: health word already).
_HEALTH_HEADS = frozenset("""pill pills meds med medication medications medicine medicines
prescription prescriptions refill refills dose doses dosage dosages inhaler inhalers
injection injections infusion infusions diagnosis diagnoses symptom symptoms flareup
flareups flare flares specialist specialists checkup checkups appointment
appointments""".split())
#: Words that only stand for "what happened" at the end of one ("the
#: lockdown drill thing", "the accident stuff").
_EVENT_FILLERS = frozenset("""thing things stuff situation incident incidents business mess
deal episode event events""".split())
#: A person's own people, singular or plural, after whom "with X" is a
#: condition ("my kid with sickle cell"), never a group ("my family with
#: board games").
_WITH_PEOPLE = frozenset("kid child baby toddler teen teenager".split())
_ARTICLES = frozenset("a an the some this that these those".split())


def _story_object(toks: list[str], seps: list[str], j: int, *,
                  own_ok: bool, first_free: bool = False, job_stop: bool = False
                  ) -> list[int]:
    """Indexes of the words a story shape points at, starting at *j*: up to
    four words, stopping at a clause break, a joining word, a pronoun or a
    word of time ("lately"); "and"/"or" joins one more word. *own_ok*: a
    my/our/his/her may come first ("deals with my anxiety"); otherwise a
    possessive there means a task, not a story ("going through my photos"),
    unless one of her people follows it ("sort out my ex ..."). An event
    verb ends the words ("since my X started"). *first_free*: the first word
    is taken even when it is such a verb ("since my will started").
    *job_stop*: a plain verb after the first word starts the job she wants
    done, which is not the story ("help my mom with dementia remember her
    garden tasks": "remember", "garden" and "tasks" are the job).
    """
    n = len(toks)
    while j < n and _base(toks[j]) in _ARTICLES and not _CLAUSE_BREAK_RE.search(seps[j]):
        j += 1
    if j < n and _base(toks[j]) in POSSESSIVES and not _CLAUSE_BREAK_RE.search(seps[j]):
        # "sort out my ex ...": one of her people is a story, not a task.
        if not own_ok and not (j + 1 < n and _kin(toks[j + 1])):
            return []
        j += 1
    out: list[int] = []
    joined = False
    stop = goal_words.stopwords()
    k = j
    while k < n and len(out) < 4:
        if _CLAUSE_BREAK_RE.search(seps[k]):
            break
        b = _base(toks[k])
        if b in ("and", "or", "&"):
            if joined or not out or k + 1 >= n:
                break
            nb = _base(toks[k + 1])
            if nb not in POSSESSIVES and (
                    nb in FUNCTION_WORDS or nb in PRONOUNS or nb in stop
                    or nb in _EVENT_VERBS or nb in _TIME_ADVERBS):
                break
            joined = True
            k += 1
            continue
        if b in POSSESSIVES and out:
            # "my PCOS and my husband's gout": one more of hers.
            k += 1
            continue
        if (not b or b in FUNCTION_WORDS or b in _TIME_ADVERBS or b in BOT_NOUNS
                or (b in PRONOUNS and b not in POSSESSIVES)):
            break
        if b in _EVENT_VERBS and (out or not first_free):
            break
        if job_stop and out and _job_verb_at(
                toks, seps, k, after_condition=_names_condition(toks[out[-1]])):
            break
        if _kin(toks[k]):
            # "my ex hits", "my husband's gambling": the person is not the
            # story, what follows is (a relative is hard anyway).
            k += 1
            continue
        out.append(k)
        k += 1
    return out


def _person_before(toks: list[str], v: int) -> bool:
    """Is there a person in front of the verb at *v* ("my wife deals", "we
    are dealing", "my son who struggles", "I have been coping")?"""
    k = v - 1
    while k >= 0 and _base(toks[k]) in _STORY_AUX:
        k -= 1
    if k < 0:
        return False
    b = _base(toks[k])
    if b == "who" and k >= 1:
        return _person_before(toks, k) or _kin(toks[k - 1]) or bool(
            _forms(toks[k - 1]) & (AUDIENCE | BEINGS))
    if b in _STORY_PEOPLE:
        return True
    raw = toks[k].lower().replace("’", "'")
    if raw.endswith("'s"):
        return False
    return _kin(b) or bool(_forms(b) & (AUDIENCE | BEINGS) and _owned(toks, k))


def _owned_here(toks: list[str], k: int) -> bool:
    """Is the word at *k* hers or one of her people's ("my thyroid", "my
    son's cystic fibrosis", "her insulin")? Up to three words back."""
    for m in range(k - 1, max(-1, k - 4), -1):
        b = _base(toks[m])
        raw = toks[m].lower().replace("’", "'")
        if b in POSSESSIVES:
            return True
        if raw.endswith("'s") and (_kin(b) or _forms(b) & (AUDIENCE | BEINGS)):
            return True
        if b in FUNCTION_WORDS or b in BOT_NOUNS or not b:
            return False
    return False


#: Verbs of having a condition ("my husband has X", "she got X").
_HAVE_VERBS = frozenset("has have had got gets developed caught contracted".split())
#: Words of how or when between the person and the verb ("my wife still
#: has", "he recently got").
_HAVE_ADVERBS = frozenset("""still also really always now recently just both all often
sometimes lately finally already""".split())
#: Who says the reason: "because I have", "since he has".
_REASON_PEOPLE = frozenset("i he she we they".split())


#: Parts of the body (grammar, like RELATIVES): what is on or in one that
#: is hers or her people's is a health detail ("the rash on my son's arm").
BODY_PARTS = frozenset("""arm arms leg legs hand hands foot feet face neck back chest skin
scalp head eye eyes ear ears nose mouth lip lips tongue tooth teeth gum gums knee knees elbow
elbows shoulder shoulders hip hips stomach belly tummy toe toes finger fingers wrist wrists
ankle ankles thigh thighs butt groin breast breasts body forehead cheek cheeks chin jaw throat
spine heel heels shin shins calf calves armpit armpits bum""".split())
_BODY_PREPS = frozenset("on in of around under behind inside near".split())
#: More words that open a reason or a time in her life, only with a person
#: after them ("after my husband had X", "given that she takes X").
_MORE_OPENERS = frozenset("after given".split())
#: Words that end a reason clause: another clause starts ("because my wife
#: takes X so I need ...", "since I started X and I want ...").
_CLAUSE_ENDS = frozenset("""so but then which that who where when while and or because since
if unless until""".split())


def _clause_rest(toks: list[str], seps: list[str], lows: list[str], v: int) -> list[int]:
    """What a person in a reason clause does or has, from *v* (the word after
    the person) to the end of the clause: every word but joining words,
    pronouns, "be"/"have" helpers, words of time and hard words, up to six.

    "since my wife started X", "because my wife takes X", "now that my dad is
    on X", "because we got a X", "since my husband tried to kill himself": X is
    held whatever it is. Grammar, not a verb list: whatever the verb, what
    follows it in a reason about her or her people is her story. The verb
    itself is held only when nothing follows it ("because my husband drinks").
    One shape is left alone: owning things said the usual way, a have verb with
    a count or a possessive after it ("because my kids have two dogs", "because
    my husband has his own quiz night"). Used only to HOLD more."""
    n = len(toks)

    def brk(x: int) -> bool:
        return bool(_CLAUSE_BREAK_RE.search(seps[x]))

    k = v
    while (k < n and not brk(k)
           and (lows[k] in _STORY_AUX or lows[k] in _HAVE_ADVERBS
                or lows[k] in ("not", "never", "just"))):
        # "has"/"have"/"had" is the verb itself unless another verb follows
        # ("has two dogs" owns; "has started X" is a helper).
        if lows[k] in _HAVE_VERBS and not (
                k + 1 < n and not brk(k + 1)
                and (lows[k + 1] in _STORY_AUX or lows[k + 1] in _HAVE_ADVERBS
                     or (_verb_like(lows[k + 1]) and lows[k + 1] not in POSSESSIVES
                         and lows[k + 1] not in PRONOUNS
                         and lows[k + 1] not in FUNCTION_WORDS))):
            break
        k += 1
    if k >= n or brk(k):
        # "because he is", "since we are": the helper was the verb.
        k = v
        if k >= n or brk(k):
            return []
    verb = k
    if (lows[verb] in _HAVE_VERBS and verb + 1 < n and not brk(verb + 1)
            and (lows[verb + 1] in POSSESSIVES or _is_count(toks[verb + 1]))):
        return []
    stop = goal_words.stopwords()
    out: list[int] = []
    m = verb + 1
    while m < n and len(out) < 6:
        if brk(m):
            break
        b = lows[m]
        if b in _CLAUSE_ENDS and out:
            break
        if (not b or b in FUNCTION_WORDS or b in PRONOUNS or b in POSSESSIVES
                or b in stop or b in _TIME_ADVERBS or b in BOT_NOUNS or b in _STORY_AUX
                or b in _CLAUSE_ENDS or is_hard_word(b)):
            m += 1
            continue
        out.append(m)
        m += 1
    if (not out and lows[verb] not in _STORY_AUX and lows[verb] not in FUNCTION_WORDS
            and not is_hard_word(lows[verb])):
        out.append(verb)
    return out


def _has_object(toks: list[str], seps: list[str], lows: list[str], v: int) -> list[int]:
    """What a person has at *v* ("has sciatica", "has Down syndrome", "is on
    lithium"), when it is said bare: never after an article, a count or a
    possessive ("has a recital", "has two dogs", "has his own truck"), which
    is how ordinary things are said."""
    n = len(toks)
    while v < n and lows[v] in _HAVE_ADVERBS and not _CLAUSE_BREAK_RE.search(seps[v]):
        v += 1
    if v >= n or _CLAUSE_BREAK_RE.search(seps[v]):
        return []
    if lows[v] in _HAVE_VERBS:
        o = v + 1
    elif lows[v] in ("is", "was", "s", "are", "were") and v + 1 < n and lows[v + 1] == "on":
        o = v + 2                     # "is on lithium"
    else:
        return []
    if o >= n or _CLAUSE_BREAK_RE.search(seps[o]):
        return []
    first = lows[o]
    if (first in _ARTICLES or first in POSSESSIVES or first in PRONOUNS or _is_count(toks[o])
            or first in FUNCTION_WORDS or first in ("no", "any", "much", "many", "lots",
                                                     "plenty", "enough", "been", "to")):
        return []
    return _story_object(toks, seps, o, own_ok=False, first_free=True)


#: Verbs after "who" that tell what one of her people started, takes or
#: has ("my son who just started X").
_WHO_STORY_VERBS = frozenset("""started starts start began begun begins begin takes take took
taking uses use used using needs need needed has have had got gets developed caught
contracted sees saw attends attended goes went lost relapsed quit stopped""".split())
#: Labels she puts in front of what she adds about herself ("Context: X").
_CONTEXT_LABELS = frozenset("""context background note notes fyi info details detail situation
backstory story history diagnosis condition conditions issue issues problem problems reason
reasons why ps update""".split())
#: The mark after such a label: a colon or a spaced dash.
_LABEL_MARK_RE = re.compile(r":|\s[-\u2013\u2014]")
#: Words that may stand in front of the label ("some context: X").
_LABEL_BEFORE = frozenset("and some a little more the my our".split())


def _label_at(lows: list[str], seps: list[str], i: int) -> bool:
    """Is the word at *i* a label she adds about herself ("Context:",
    "FYI -", "some background:"): a label word with a colon or a spaced dash
    after it, at the start of a sentence or part, or after "some"/"more"?"""
    return (lows[i] in _CONTEXT_LABELS and i + 1 < len(lows)
            and bool(_LABEL_MARK_RE.search(seps[i + 1]))
            and (i == 0 or bool(_CLAUSE_BREAK_RE.search(seps[i]))
                 or lows[i - 1] in _LABEL_BEFORE))


def _her_person_before(toks: list[str], seps: list[str], v: int) -> bool:
    """Is one of her people (a relative, "he"/"she", "my son") right in front
    of the verb at *v*, past "still"/"just"/"also" and helpers, in the same
    clause? Never "I"/"we": "I have a big garden" is her own plain news."""
    k = v - 1
    while k >= 0 and not _CLAUSE_BREAK_RE.search(seps[k + 1]) and (
            _base(toks[k]) in _HAVE_ADVERBS or _base(toks[k]) in ("has", "have", "had")):
        k -= 1
    if k < 0 or _CLAUSE_BREAK_RE.search(seps[k + 1]):
        return False
    raw = toks[k].lower().replace("\u2019", "'")
    b = _base(toks[k])
    if b in ("he", "she", "he's", "she's"):
        return True
    if raw.endswith("'s"):
        # "my son's got X" is "my son has got X"; "my son's teacher" is not.
        return _kin(b) and _base(toks[v]) in ("got", "had", "been")
    return _kin(toks[k]) or bool(_forms(b) & (AUDIENCE | BEINGS) and _owned(toks, k))


def _names_condition(tok: str) -> bool:
    """Is *tok* a health word or a listed sensitive word on its own ("diabetes",
    "lupus", "ocd")? Then the condition is said in full and a plain verb after
    it starts the job."""
    b = _base(tok)
    return bool(b) and bool(is_health_word(b) or sensitive_category(b))


def _job_verb_at(toks: list[str], seps: list[str], k: int, *,
                 after_condition: bool = False) -> bool:
    """Is the word at *k* a plain verb that starts a job ("remember her
    garden tasks", "plan weekly grocery trips", "learn to tie")? A plain
    form the spelling list makes a past or "-ing" form of, never a listed
    sensitive or health word, and followed in the same clause by a word that
    is not another such verb ("chronic pain manage ..." stops at "manage").
    *after_condition*: the word before it is a whole condition ("diabetes"),
    so the next word may be a verb too ("bake low sugar cookies", "plan short
    walks"; otherwise "bake" and "low" are listed as kept private). "low
    vision" is not cut: "low" is not a condition on its own."""
    def plain_verb(x: int) -> bool:
        b = _base(toks[x])
        return (bool(b) and b.isalpha() and _dictionary().get(b) == "common"
                and b not in _ALSO_NAMES and not b.endswith(("s", "ed", "ing"))
                and b not in FUNCTION_WORDS and _has_verb_forms(b)
                and not sensitive_category(b) and not is_health_word(b))
    if not plain_verb(k):
        return False
    if k + 1 >= len(toks) or _CLAUSE_BREAK_RE.search(seps[k + 1]):
        return False
    if after_condition:
        return True
    return not plain_verb(k + 1) or _base(toks[k + 1]) in FUNCTION_WORDS


def _job_after(toks: list[str], seps: list[str], lows: list[str], end: int) -> list[int]:
    """The words of the job that starts at *end*, right after a person's
    condition: "to" + a verb, or a plain verb ("stop crying", "to count
    carbs", "avoid bars"), to the end of the clause. ``[]`` when no job
    starts there."""
    n = len(toks)
    if end >= n or _CLAUSE_BREAK_RE.search(seps[end]):
        return []
    start = -1
    if lows[end] == "to" and end + 1 < n and not _CLAUSE_BREAK_RE.search(seps[end + 1]):
        start = end + 1
    elif _job_verb_at(toks, seps, end, after_condition=True) or _verb_like(lows[end]):
        start = end
    if start < 0:
        return []
    out: list[int] = []
    for k in range(start, n):
        if k > start and _CLAUSE_BREAK_RE.search(seps[k]):
            break
        b = lows[k]
        if k > start and b in _CLAUSE_ENDS:
            break
        if not b or b in FUNCTION_WORDS or b in POSSESSIVES or b in PRONOUNS or b in BOT_NOUNS:
            continue
        out.append(k)
    return out


#: Verbs of living with a condition: what one of her people does about it
#: ("avoid bars", "count calories", "stop purging", "ignore voices", "take
#: his pills", "track ovulation"). A job after "<her person> with
#: <condition>" that starts with one of these tells the story.
_MANAGE_VERBS = frozenset("""avoid avoids avoiding stop stops stopping quit quits quitting
ignore ignores ignoring resist resists resisting manage manages managing control controls
controlling cope copes coping handle handles handling reduce reduces reducing limit limits
limiting cut cuts cutting count counts counting track tracks tracking monitor monitors
monitoring log logs logging measure measures measuring check checks checking take takes
taking skip skips skipping remember remembers calm calms soothe soothes deal deals dealing
survive survives get gets lose loses losing gain gains gaining fight fights fighting beat
beats overcome overcomes""".split())


#: What a diet for a condition changes ("low sugar", "salt free").
_NUTRIENTS = frozenset("""sugar sugars salt sodium carb carbs carbohydrate carbohydrates fat fats
cholesterol gluten dairy lactose calorie calories caffeine potassium protein""".split())


def _job_tells_story(toks: list[str], lows: list[str], job: list[int]) -> bool:
    """Does the job said right after one of her people's condition tell
    their story? Yes when its verb manages the condition (see
    :data:`_MANAGE_VERBS`) or any of its words is a health, diet or listed
    sensitive word ("chemo", "carbs", "meds"). Fail closed: an unknown
    shape (no plain verb first) counts as story."""
    if not job:
        return False
    first = lows[job[0]]
    if first in _MANAGE_VERBS:
        return True
    for k in job:
        b = lows[k]
        if (is_health_word(b) or sensitive_category(b) or _forms(b) & DIET_WORDS
                or _forms(b) & _HEALTH_HEADS or medical_shape(b)
                or _forms(b) & _NUTRIENTS):
            # "bake low sugar cookies" for a grandma with diabetes: a diet
            # changed for the condition manages it.
            return True
    d = _dictionary()
    return not (d.get(first) == "common" and _has_verb_forms(first))


def _plain_job_conditions(text: str) -> set[str]:
    """Condition words of "<her person> with <condition> <plain pastime>"
    ("my dad with copd to plan easy fishing trips": {"copd"}). The condition
    is still held where it sits; it only does not make the whole goal a
    private story, so the pastime is searched."""
    out: set[str] = set()
    for off, sent in _spans(text, _SENT_RE):
        toks, seps, _ = _tok_seps(sent, off)
        lows = [_base(t) for t in toks]
        n = len(toks)
        for i in range(1, n - 1):
            if lows[i] != "with" or _CLAUSE_BREAK_RE.search(seps[i]):
                continue
            who = toks[i - 1]
            if not ((_kin(who) or (lows[i - 1] in _WITH_PEOPLE and _owned(toks, i - 1)))
                    and not who.lower().endswith("'s")):
                continue
            if lows[i + 1] in POSSESSIVES or lows[i + 1] in PRONOUNS:
                continue
            idx = _story_object(toks, seps, i + 1, own_ok=False, job_stop=True)
            if not idx or not any(_names_condition(toks[x]) for x in idx):
                continue
            job = _job_after(toks, seps, lows, idx[-1] + 1)
            if job and not _job_tells_story(toks, lows, job):
                for x in idx:
                    out |= _forms(lows[x])
    return out


def _story_holds(text: str) -> list[str]:
    """Words of *text* that one of the story shapes points at (see above):

    * a frame after one of her people ("my wife deals with X", "we are
      sorting out X", "my son who is coping with X"), or any frame that is a
      story whoever says it ("diagnosed with X", "recovering from X");
    * "since my X", "because of my X", "due to my X" (and "since the X
      started": an article needs the event verb);
    * "my kid with X", "my son with X" (her people, never a group);
    * the word in front of a medicine or symptom word when it is hers ("my
      thyroid pills", "his seizure meds").

    Returns the words as typed (lower case). Used only to HOLD more."""
    out: list[str] = []
    personal = _goal_is_personal(goal_words.raw_words(text))

    def take(idx: list[int], toks: list[str]) -> None:
        for k in idx:
            b = _base(toks[k])
            if b and not is_hard_word(b) and b not in out:
                out.append(b)

    for off, sent in _spans(text, _SENT_RE):
        toks, seps, _ = _tok_seps(sent, off)
        lows = [_base(t) for t in toks]
        n = len(toks)
        for i in range(n):
            b = lows[i]
            brk = _CLAUSE_BREAK_RE.search(seps[i + 1]) if i + 1 < n else True
            # Frames: "<verb> with|from|out|through|for".
            if i + 1 < n and not brk and lows[i + 1] in _STORY_FRAMES \
                    and b in _STORY_FRAMES[lows[i + 1]]:
                if b in _STORY_ANY or _person_before(toks, i):
                    own_ok = lows[i + 1] not in ("out", "through")
                    take(_story_object(toks, seps, i + 2, own_ok=own_ok,
                                       first_free=True), toks)
            # "battling X" with no particle.
            if b in ("battling", "battles", "battled") and _person_before(toks, i) \
                    and i + 1 < n and lows[i + 1] not in _STORY_FRAMES:
                take(_story_object(toks, seps, i + 1, own_ok=True, first_free=True), toks)
            # "since my X ...", "because of my X", "due to my X", "after my
            # X", "now that my X ...", "given that ...".
            opener = b in _STORY_OPENERS or b in _MORE_OPENERS or (
                b == "now" and i + 1 < n and lows[i + 1] == "that"
                and not _CLAUSE_BREAK_RE.search(seps[i + 1]))
            if opener:
                j = i + 2 if b == "now" else i + 1
                if j < n and lows[j] in ("of", "to", "that") \
                        and not _CLAUSE_BREAK_RE.search(seps[j]):
                    j += 1
                if j < n and not _CLAUSE_BREAK_RE.search(seps[j]):
                    first = lows[j]
                    # "because my husband has X", "since I have X", "because
                    # she is on X": the reason clause is her story whatever X
                    # is. "since my wife started X", "because my wife takes
                    # X", "because we got a X", "now that my dad is on X":
                    # whatever verb follows her or her people, what comes after it
                    # in that clause is her story (see _clause_rest).
                    p_end = -1
                    if first in _REASON_PEOPLE or _kin(toks[j]):
                        p_end = j
                    elif first == "there" and j + 1 < n and lows[j + 1] in (
                            "is", "are", "was", "were", "s"):
                        p_end = j
                    elif _is_cap(toks[j]) and first not in FUNCTION_WORDS \
                            and dict_kind(first) != "common" \
                            and not toks[j].lower().replace("’", "'").endswith("'s"):
                        p_end = j        # "because Liam has X": a name
                    elif first in POSSESSIVES:
                        for q in range(j + 1, min(n, j + 4)):
                            if _CLAUSE_BREAK_RE.search(seps[q]):
                                break
                            raw_q = toks[q].lower().replace("’", "'")
                            if raw_q.endswith("'s"):
                                break
                            if _kin(toks[q]) or _forms(lows[q]) & (AUDIENCE | BEINGS):
                                p_end = q
                                break
                            if lows[q] in FUNCTION_WORDS:
                                break
                    if p_end >= 0:
                        take(_clause_rest(toks, seps, lows, p_end + 1), toks)
                    # "after his X", "after her X", "after my dad's X": what
                    # happened to one of her people, said last or as it
                    # began, is her story whatever X is ("a meal bot for my
                    # dad after his stroke"). Not "after my X": "after my
                    # shift" and "after my run" are her plain day.
                    after_theirs = b == "after" and first in POSSESSIVES and j + 1 < n and (
                        first in ("his", "her", "their")
                        or (_kin(toks[j + 1])
                            and toks[j + 1].lower().replace("’", "'").endswith("'s")))
                    if first in POSSESSIVES and (b in _STORY_OPENERS or after_theirs):
                        k = j + 1
                        # "since my son's diagnosis": past the relative's name.
                        if k < n and _kin(toks[k]) and toks[k].lower().replace(
                                "’", "'").endswith("'s"):
                            k += 1
                        elif k < n and (_kin(toks[k]) or _forms(lows[k]) & (AUDIENCE | BEINGS)):
                            k = n       # "because my wife deals ...": a frame's job
                        if k < n:
                            idx = _story_object(toks, seps, k, own_ok=True,
                                                first_free=True)
                            end = (idx[-1] + 1) if idx else n
                            # A noun reason ("because of my X", "due to my
                            # X"), X said last ("since my X."), or X began
                            # ("since my X started").
                            last = (end >= n or bool(_CLAUSE_BREAK_RE.search(seps[end]))
                                    or lows[end] in FUNCTION_WORDS)
                            if idx and (lows[i + 1] in ("of", "to") or last
                                        or lows[end] in _ONSET_VERBS):
                                take(idx, toks)
                    elif first in _ARTICLES and b in _STORY_OPENERS:
                        idx = _story_object(toks, seps, j, own_ok=False)
                        end = (idx[-1] + 1) if idx else j + 1
                        if idx and end < n and lows[end] in _ONSET_VERBS \
                                and not _CLAUSE_BREAK_RE.search(seps[end]):
                            take(idx, toks)
                    elif b == "after" and first == "the" and personal:
                        # "a homework helper for my kid after the accident":
                        # in a goal about her people, the thing said last
                        # after "after the" is their event, whatever it is
                        # ("after the stroke", "after the cutting"). Fail
                        # closed: "after the game" holds "game" too.
                        idx = _story_object(toks, seps, j, own_ok=False)
                        end = (idx[-1] + 1) if idx else n
                        # "after the lockdown drill thing": a filler word at
                        # the end does not make the event longer.
                        core = list(idx)
                        while len(core) > 1 and lows[core[-1]] in _EVENT_FILLERS:
                            core.pop()
                        if core and len(core) <= 2 and (
                                end >= n or _CLAUSE_BREAK_RE.search(seps[end])
                                or lows[end] in _CLAUSE_ENDS):
                            take(idx, toks)
            # "my son who has X".
            if b == "who" and i >= 1 and not _CLAUSE_BREAK_RE.search(seps[i]) \
                    and not toks[i - 1].lower().replace("’", "'").endswith("'s") \
                    and (_kin(toks[i - 1]) or (_forms(lows[i - 1]) & (AUDIENCE | BEINGS)
                                               and _owned(toks, i - 1))):
                take(_has_object(toks, seps, lows, i + 1), toks)
                # "my son who just started X", "my mom who has a X order",
                # "my wife who takes X": what one of her people started,
                # takes, uses or has is her story, whatever X is ("clomid",
                # "dnr", "puberty blockers"). Fail closed: an everyday thing there
                # ("who just started piano lessons") is held too; the bot's
                # subject is still searched. Never what they like, play or
                # struggle with ("who struggles with fractions" needs a
                # fractions bot).
                v = i + 1
                while v < n and not _CLAUSE_BREAK_RE.search(seps[v]) and (
                        lows[v] in _STORY_AUX or lows[v] in _HAVE_ADVERBS) \
                        and lows[v] not in _HAVE_VERBS:
                    v += 1
                if v + 1 < n and not _CLAUSE_BREAK_RE.search(seps[v]) \
                        and lows[v] in _WHO_STORY_VERBS \
                        and not _CLAUSE_BREAK_RE.search(seps[v + 1]):
                    take(_story_object(toks, seps, v + 1, own_ok=True, first_free=True),
                         toks)
                # "my teen who has been cutting", "my husband who is drinking
                # again": what one of her people has been doing, said on its
                # own with nothing after it, is her story whatever the verb
                # is. A
                # verb with a thing after it ("who is learning piano") is
                # left to the rules above.
                w_ = i + 1
                while w_ < n and not _CLAUSE_BREAK_RE.search(seps[w_]) and (
                        lows[w_] in _STORY_AUX or lows[w_] in _HAVE_ADVERBS
                        or lows[w_] in ("has", "have", "had")):
                    w_ += 1
                if w_ > i + 1 and w_ < n and not _CLAUSE_BREAK_RE.search(seps[w_]) \
                        and lows[w_].endswith("ing") and len(lows[w_]) > 4:
                    nx = w_ + 1
                    while nx < n and not _CLAUSE_BREAK_RE.search(seps[nx]) and (
                            lows[nx] in _TIME_ADVERBS or lows[nx] in ("again", "herself",
                                                                      "himself", "themselves",
                                                                      "a", "lot")):
                        nx += 1
                    if nx >= n or _CLAUSE_BREAK_RE.search(seps[nx]) \
                            or lows[nx] in _CLAUSE_ENDS:
                        take([w_], toks)
            # "my brother has X", "he got X", "my mom is on X" in any clause:
            # what one of her people has, said bare, whatever it is ("my
            # brother has bunions so ...", "he got shingles and ..."; fail closed, so
            # "my son has soccer practice" holds "soccer" too). Never "has a
            # recital", "has two dogs", "has his own truck" (see _has_object).
            if (b in _HAVE_VERBS or (b in ("is", "was", "s") and i + 1 < n
                                     and lows[i + 1] == "on")) \
                    and _her_person_before(toks, seps, i):
                take(_has_object(toks, seps, lows, i), toks)
            # "Context: X", "Background - X", "FYI: X": what she adds after
            # a label is about her, whatever it is. Every word to the sentence's end.
            if _label_at(lows, seps, i):
                rest = [k for k in range(i + 1, n)
                        if lows[k] and lows[k] not in FUNCTION_WORDS and lows[k] not in PRONOUNS
                        and lows[k] not in BOT_NOUNS and not is_hard_word(lows[k])]
                take(rest, toks)
            # "because of X", "due to X": the reason she gives is hers,
            # whatever it is ("a budget bot because of mold toxicity").
            # Every word to the clause's end.
            if b in ("because", "due") and i + 2 < n and lows[i + 1] in ("of", "to") \
                    and not _CLAUSE_BREAK_RE.search(seps[i + 1]) \
                    and not _CLAUSE_BREAK_RE.search(seps[i + 2]):
                got: list[int] = []
                for k in range(i + 2, n):
                    if k > i + 2 and _CLAUSE_BREAK_RE.search(seps[k]):
                        break
                    if lows[k] in _CLAUSE_ENDS and lows[k] not in ("and", "or") and got:
                        break
                    if (not lows[k] or lows[k] in FUNCTION_WORDS or lows[k] in PRONOUNS
                            or lows[k] in BOT_NOUNS or is_hard_word(lows[k])):
                        continue
                    got.append(k)
                    if len(got) >= 6:
                        break
                take(got, toks)
            # "my kid with X", "my son with X".
            if b == "with" and i >= 1 and not _CLAUSE_BREAK_RE.search(seps[i]):
                who = toks[i - 1]
                if (_kin(who) or (lows[i - 1] in _WITH_PEOPLE and _owned(toks, i - 1))) \
                        and not who.lower().endswith("'s"):
                    j = i + 1
                    if j < n and lows[j] not in POSSESSIVES and lows[j] not in PRONOUNS:
                        idx = _story_object(toks, seps, j, own_ok=False, job_stop=True)
                        take(idx, toks)
                        # The job after the condition ("my wife with
                        # depression stop crying", "my kid with diabetes to
                        # count carbs") tells the story too: held, unless the
                        # bot's subject names it. So that "my uncle with
                        # arthritis grow tomatoes on the balcony" still sends
                        # something, only a job that manages the
                        # condition ("avoid bars", "count carbs", "stop
                        # crying", "take his pills") or names a health or
                        # sensitive thing tells the story. A plain pastime
                        # ("grow tomatoes", "find hiking trails", "sort her
                        # stamp collection") is searched.
                        if idx and any(_names_condition(toks[x]) for x in idx):
                            job = _job_after(toks, seps, lows, idx[-1] + 1)
                            if job and _job_tells_story(toks, lows, job):
                                take(job, toks)
            # "the rash on my son's arm", "a lump in her breast": a part of
            # her or her people's body, and what is on or in it (so "rash
            # arm" does not go out).
            if b in BODY_PARTS and i >= 2:
                m = i - 1
                owned_part = False
                while m >= 0 and not _CLAUSE_BREAK_RE.search(seps[m + 1]) and (
                        lows[m] in POSSESSIVES or (_kin(toks[m]) or _forms(lows[m])
                                                   & (AUDIENCE | BEINGS))):
                    owned_part = True
                    m -= 1
                if owned_part and m >= 1 and lows[m] in _BODY_PREPS \
                        and not _CLAUSE_BREAK_RE.search(seps[m]):
                    got = [i]
                    q = m - 1
                    while q >= 0 and len(got) < 3 and not _CLAUSE_BREAK_RE.search(seps[q + 1]):
                        if (lows[q] in _ARTICLES or lows[q] in FUNCTION_WORDS or lows[q] in PRONOUNS or lows[q] in BOT_NOUNS
                                or lows[q] in POSSESSIVES or is_hard_word(lows[q])
                                or _verb_like(lows[q]) or lows[q] in goal_words.stopwords()):
                            break
                        got.append(q)
                        q -= 1
                    if len(got) > 1:
                        take(got, toks)
            # "my thyroid pills": the word in front of a medicine word.
            if b in _HEALTH_HEADS and i >= 1 and not _CLAUSE_BREAK_RE.search(seps[i]):
                k = i - 1
                if lows[k] not in FUNCTION_WORDS and lows[k] not in POSSESSIVES \
                        and not _kin(toks[k]) and _owned_here(toks, k):
                    take([k], toks)
                    if k >= 1 and not _CLAUSE_BREAK_RE.search(seps[k]) \
                            and lows[k - 1] not in FUNCTION_WORDS \
                            and lows[k - 1] not in POSSESSIVES and not _kin(toks[k - 1]) \
                            and not toks[k - 1].lower().endswith("'s"):
                        take([k - 1], toks)
    return out


def date_phrase_hits(words: list[str]) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """The days of the year of more than one word in *words* ("memorial
    day", "new year"), with the words only that day uses."""
    low = [_base(w) for w in words]
    return [(seq, own) for seq, own in DATE_PHRASES if contains_seq(low, seq)]


def _date_positions(words: list[str]) -> set[int]:
    """Indexes in *words* that are part of a day of the year of more than
    one word ("memorial" in "memorial day" is a date, not grief)."""
    low = [goal_words.singular(_base(w)) for w in words]
    out: set[int] = set()
    for seq, _own in DATE_PHRASES:
        target = [goal_words.singular(x) for x in seq]
        n = len(target)
        for i in range(len(low) - n + 1):
            if low[i:i + n] == target:
                out |= set(range(i, i + n))
    return out


#: Places of a home or a life she may say are hers ("in our den", "at my
#: cabin"): held until she ticks them. Not "garden" or "kitchen" alone: those
#: are what many bots are about and stay in the subject anyway.
_HOME_PLACES = frozenset("""den dens garage garages basement basements attic attics bedroom
bedrooms bathroom bathrooms house houses apartment apartments flat condo condos cabin cabins
cottage cottages barn barns shed sheds yard backyard porch office studio shop workshop cellar
hallway driveway farm ranch camper rv dorm loft playroom nursery""".split())


#: Spelled-out ordinals ("tenth", the "fifth" of "twenty-fifth").
_ORDINALS = frozenset("""first second third fourth fifth sixth seventh eighth ninth tenth
eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth
twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth hundredth
""".split())
#: Names for how long a marriage has lasted ("our golden anniversary").
_ANNIVERSARY_NAMES = frozenset("""golden silver ruby diamond pearl crystal sapphire emerald
coral jade tin paper wooden""".split())
#: The days of her people's lives an ordinal counts ("our tenth anniversary",
#: "his fortieth birthday").
_COUNTED_DAYS = frozenset("""anniversary anniversaries birthday birthdays bday bdays jubilee
""".split())
#: Words a model may say about a relative who died ("estate sale"); held in
#: a goal that says one of her people died ("my late aunt"), never listed
#: (she did not type them), so "late sewing" and "estate sale" do not go
#: out for "my late aunt's sewing machines".
_AFTER_A_DEATH = frozenset("""late estate estates inheritance inherited inherit heirloom
heirlooms probate executor executors funeral funerals memorial memorials obituary
obituaries urn urns condolence condolences sympathy bereavement deceased passing
widow widowed widower""".split())


def _death_of_hers(craw: list[str], k: int) -> bool:
    """Is "late" at *k* said of one of her people ("my late aunt", "our late
    father's", "his late wife")? A late bus, a late shift or a late fee is
    not."""
    if craw[k] != "late" or k + 1 >= len(craw):
        return False
    return _kin(craw[k + 1]) or (craw[k + 1] in ("husband's", "wife's"))


def _goal_marks(goal: str, home: dict | None) -> tuple[_Marks, bool, set[str]]:
    """Mark every personal word of *goal*. Returns (marks, holds_place, places).

    The same goal is read many times per screen (the subject's job words
    read it too), so the answer for the last goals is kept. The
    result is never changed by a caller."""
    try:
        key = json.dumps(home, sort_keys=True, default=str) if home else ""
    except (TypeError, ValueError):
        return _goal_marks_now(goal, home)
    # A changed spelling list (tests swap it) is a different reading.
    key += f"|{id(_DICT)}|{len(_DICT or ())}|{id(dict_kind)}|{id(_load_dict)}"
    return _goal_marks_cached(str(goal or ""), key, home)


_MARKS_CACHE: "collections.OrderedDict[tuple[str, str], tuple]" = collections.OrderedDict()
_MARKS_LOCK = threading.Lock()


def _goal_marks_cached(goal: str, key: str, home: dict | None) -> tuple[_Marks, bool, set[str]]:
    with _MARKS_LOCK:
        got = _MARKS_CACHE.get((goal, key))
        if got is not None:
            _MARKS_CACHE.move_to_end((goal, key))
            return got
    got = _goal_marks_now(goal, home)
    with _MARKS_LOCK:
        _MARKS_CACHE[(goal, key)] = got
        while len(_MARKS_CACHE) > 64:
            _MARKS_CACHE.popitem(last=False)
    return got


def _goal_marks_now(goal: str, home: dict | None) -> tuple[_Marks, bool, set[str]]:
    """Mark every personal word of *goal* (see :func:`_goal_marks`)."""
    marks = _Marks()
    places: set[str] = set()
    stop = goal_words.stopwords()
    text = _fold(goal)
    goal_raw = goal_words.raw_words(text)

    def not_filler(word: str) -> bool:
        b = _base(word)
        return bool(b) and b not in stop and b not in SUBJECT_FILLER

    for off, sent in _spans(text, _CUE_SENT_RE):
        toks, seps, starts = _tok_seps(sent, off)

        def hold_name(k: int, strength: str, person: bool = False,
                      surely: bool = False) -> None:
            """*person*: a cue says it is someone; a strong name is then held
            everywhere. *surely*: so is a weak one (it does a person's thing:
            "River needs ...")."""
            if strength:
                marks.mark_held(toks[k], "name", True)
                if strength == "strong":
                    marks.strong.add(starts[k])
                if person and (strength == "strong" or surely):
                    marks.person.update(_forms(toks[k]))

        def spread(j: int, strength: str, person: bool = False) -> None:
            """A list of names: "Grace and Max", "Grace, Max", "Anna Lee"."""
            k = j + 1
            while k < len(toks):
                b = _base(toks[k])
                if b in ("and", "or", "&") and k + 1 < len(toks):
                    cand = k + 1
                elif "," in seps[k] or (_is_cap(toks[k]) and _is_cap(toks[k - 1])):
                    cand = k
                else:
                    break
                got = _name_strength(toks[cand], cued=False)
                # So "my daughter Emma, plan her party" does not hold
                # "plan": after a capitalised name, a lower-
                # case ordinary word is not one more name, after a comma too.
                if not got or (got == "weak" and strength != "weak"):
                    # "Grace and her ...": stop. A lower-case word after a
                    # capitalised name and "and" is left alone ("my son Leo
                    # and chess"); after a lower-case name it is one more
                    # ("my kids grace and mark").
                    break
                hold_name(cand, got, person)
                k = cand + 1

        for i, tok in enumerate(toks):
            if tok == "&":
                continue
            low = tok.lower()
            base = _base(tok)
            prev = toks[i - 1].lower() if i else ""
            # A new line starts a new thought for capitals (bulleted lists),
            # but never ends a name cue ("my daughter\nRose").
            line_start = i == 0 or "\n" in seps[i] or "\r" in seps[i]
            why = is_hard_word(base)
            nxt = _base(toks[i + 1]) if i + 1 < len(toks) else ""
            if why:
                marks.mark_hard(base, why)
            elif _is_plan_time(base, prev, nxt,
                               _base(toks[i - 2]) if i > 1 else "",
                               _base(toks[i + 2]) if i + 2 < len(toks) else "") \
                    and not (_is_cap(tok) and not line_start and i + 1 < len(toks)
                             and _is_cap(toks[i + 1])
                             and not (_forms(nxt) & (PLAN_NOUNS | PERIOD_NOUNS))):
                # A capitalised "Spring" in front of another capitalised
                # word is a product's name ("Spring Boot"), not a season;
                # "Spring Break" is still one.
                marks.mark_date(base)
            elif _has_digit(base):
                marks.mark_hard(base, "number")
            elif tok == "May" and not line_start:
                marks.mark_hard(base, "date")
            elif base in AUDIENCE and (prev in POSSESSIVES or prev in NUMBER_WORDS
                                       or prev.isdigit()):
                marks.mark_hard(base, "relative")
            # A named day of the year: capitalised words in front of "Day",
            # "Eve" or "Weekend" ("Presidents Day", "Martin Luther King Day",
            # "Mother's Day").
            if tok in _DAY_HEADS and i > 0 and _is_cap(toks[i - 1]) \
                    and not ("\n" in seps[i] or "\r" in seps[i]):
                k = i
                run: list[str] = []
                while k >= 0 and _is_cap(toks[k]) and _base(toks[k]) not in (
                        "i", "a", "an", "the", "my", "our") and len(run) < 5:
                    run.insert(0, toks[k])
                    # A line break or a comma ends the name of the day.
                    if "\n" in seps[k] or "\r" in seps[k] or _CLAUSE_BREAK_RE.search(seps[k]):
                        break
                    k -= 1
                seq = tuple(x for r in run for x in goal_words.raw_words(r) if x != "s")
                if len(seq) > 1 and seq not in marks.date_seqs:
                    marks.date_seqs.append(seq)
                for r in run:
                    b = _base(r)
                    if b and not is_hard_word(b):
                        marks.mark_date(b)
            # A name the goal gives outright: "named Biscuit", "called Sweet
            # Rise", "her name is Grace", "his name's Leo", "goes by Max".
            name_cue = (prev in NAME_CUES or prev == "name's"
                        or (prev in ("is", "was", "are") and i > 1
                            and _base(toks[i - 2]) in NAME_NOUNS)
                        or (prev == "by" and i > 1 and toks[i - 2].lower() == "goes"))
            if name_cue and not why:
                got = _name_strength(tok, cued=True)
                if got:
                    hold_name(i, got, True)
                    spread(i, got, True)
            # A possessive name ("Jake's") and "I'm Leo". A condition named
            # after a person ("Parkinson's") is a health word, held below.
            if (_is_cap(tok) and low.endswith("'s") and not _kin(base)
                    and base not in FUNCTION_WORDS and not is_health_word(tok)):
                hold_name(i, "strong")
            # A household thing one of her people owns: "my mom's dryer", "my
            # brother Gil's barn", "Walt's walker" (so "broken knob dryer"
            # does not go to GitHub and npm from "the broken knob on my mom's
            # dryer"). Held until she
            # ticks it; the subject still keeps it. Only things of a home
            # (HOUSEHOLD_NOUNS): "my son's birthday party" and "my daughter's
            # recital" are what such bots are about and stay searchable.
            raw_prev = toks[i - 1].lower().replace("’", "'") if i else ""
            prev_base = _base(toks[i - 1]) if i else ""
            # Also after "his"/"her"/"their" ("his Cub Scout den"), up to
            # three words on.
            theirs = prev_base in ("his", "her", "their") and not raw_prev.endswith("'s")
            if (not why and prev_base and (raw_prev.endswith("'s") or theirs)
                    and not ("\n" in seps[i] or "\r" in seps[i]
                             or _CLAUSE_BREAK_RE.search(seps[i]))
                    and (theirs or _kin(prev_base) or (_is_cap(toks[i - 1])
                                                       and prev_base not in FUNCTION_WORDS
                                                       and not is_health_word(toks[i - 1])))):
                k = i
                run: list[int] = []
                while k < len(toks) and k < i + 3:
                    if k > i and ("\n" in seps[k] or _CLAUSE_BREAK_RE.search(seps[k])):
                        break
                    b = _base(toks[k])
                    if (not b or b in FUNCTION_WORDS or b in PRONOUNS or b in POSSESSIVES
                            or b in BOT_NOUNS or is_hard_word(b) or b in _STORY_AUX
                            or b in _CLAUSE_ENDS):
                        break
                    run.append(k)
                    if _forms(b) & HOUSEHOLD_NOUNS:
                        # The words in front of the thing say what it is
                        # ("pill" of "my mom's pill cabinet"): held with it.
                        for x in run:
                            marks.mark_held(toks[x], "private", True)
                        run = []
                    k += 1
            i_am = prev in ("i'm", "im") or (i > 1 and toks[i - 2].lower() == "i"
                                              and prev == "am")
            if i_am and _is_cap(tok) and not why:
                hold_name(i, "strong", True)
            # A capitalised word followed by an age: "Rose is 8 and wants",
            # "Rose turns 8 soon", "Rose, 8, loves".
            if not why and _is_cap(tok) and _age_follows(toks, seps, i):
                got = _name_strength(tok, cued=True)
                if got:
                    hold_name(i, "strong", True)
            # Right after one of her people or pets: "my daughter Grace", "my
            # step-daughter Rose", "my 8-year-old Grace", "my son Will", "our
            # puppy Biscuit", "my kids grace and mark". Held like every other
            # name the goal gives (rule 2: only her own tick lets it out). A
            # lower-case ordinary word there ("my son river", "my son sky",
            # "my daughter piano lessons") may be a name or not: it is held
            # too (fail closed; she can tick it), but stays in the subject
            # when it sits there ("my son soccer coach bot"). Many given
            # names are ordinary words ("river", "sky", "willow", "sage"),
            # so the spelling list alone cannot release them.
            if not why and _person_at(toks, i - 1):
                got = _name_strength(tok, cued=False)
                # So "my son plays chess" does not list "plays" as a name:
                # a lower-case word
                # the spelling list knows as a verb form is what they do,
                # not who they are. Words that are also names ("will",
                # "grace", "mark") stay held.
                if (got == "weak" and _verb_like(base)) or (
                        not _is_cap(tok) and base in LIFE_EVENTS):
                    got = ""
                if got:
                    hold_name(i, got, True)
                    spread(i, got, True)
            # The one who does a person's thing at the start of a clause, in
            # any case: "River needs a swim bot", "Mia and Leo need ...",
            # "liam wants ...", "help Sage remember ..." (so "river swim
            # training", "mia reading" and "liam chores" are not sent). Each
            # is held everywhere.
            if not why:
                for k, got in _person_subject_run(toks, seps, i):
                    hold_name(k, got, True, surely=True)
            # A list under a label that names people ("Kids:\nMarsh\nOcean",
            # "Players: Reed, Pearl and Sky"): every item is a name, ordinary
            # word or not (so "marsh" and "ocean" do not go out).
            if (_forms(base) & _PEOPLE_LABELS and i + 1 < len(toks)
                    and ":" in seps[i + 1]):
                k, count = i + 1, 0
                while k < len(toks) and count < 8:
                    b = _base(toks[k])
                    if b in ("and", "&", "or") and k > i + 1:
                        k += 1
                        continue
                    if k > i + 1 and not (re.search(r"[,;\n\r]", seps[k])
                                          or _base(toks[k - 1]) in ("and", "&", "or")):
                        break
                    got = _person_word(toks[k])
                    if not got:
                        break
                    hold_name(k, got, True, surely=True)
                    count += 1
                    k += 1
            # A lower-case name after "for"/"with" ("a party planner bot for
            # emma", "for olivia and noah") and a name's possessive typed
            # without the apostrophe ("plan emmas party").
            if not why and prev in _LOWER_NAME_CUES:
                got = _lower_name(toks, seps, i)
                if got:
                    hold_name(i, got)
                    spread(i, got)
            if not why and _lower_possessive(toks, seps, i):
                # Sure when what is left is a name ("emmas"); an unknown word
                # ("jaydens", "postgres") is held but may still name the bot
                # ("a postgres backup bot").
                hold_name(i, "strong" if dict_kind(_base(tok)[:-1]) == "proper" else "weak")
                marks.mark_held(_base(tok)[:-1], "name", True)
            # Right before one of her people: "Grace, my daughter", "Grace is
            # my daughter", "Rose, my wife, wants", "Biscuit, my dog".
            if not why and i + 1 < len(toks):
                j = i + 1
                is_cue = _base(toks[j]) in ("is", "was", "are")
                if is_cue:
                    j += 1
                comma = "," in seps[i + 1] or "(" in seps[i + 1]
                if _person_ahead(toks, j):
                    got = _name_strength(tok, cued=is_cue or comma)
                    # Lower case needs the comma or "is" ("grace, my
                    # daughter"); a capital needs neither mid-sentence. The
                    # first word of a sentence ("Help my daughter") needs one.
                    if got == "strong" and (is_cue or comma
                                            or (not line_start and _is_cap(tok))):
                        hold_name(i, "strong", True)
                        # "Grace and Max, my kids": the names before it too.
                        k = i
                        while (k >= 2 and _base(toks[k - 1]) in ("and", "or", "&")
                               and _is_cap(toks[k - 2])
                               and _name_strength(toks[k - 2], cued=False) == "strong"):
                            hold_name(k - 2, "strong", True)
                            k -= 2
                        while (k >= 1 and "," in seps[k] and _is_cap(toks[k - 1])
                               and _name_strength(toks[k - 1], cued=False) == "strong"):
                            hold_name(k - 1, "strong", True)
                            k -= 1
            # A pet's or a thing's own name after "my <noun>": "my shop Sweet
            # Rise", "our boat Lady Jane".
            if (i >= 2 and _is_cap(tok) and not why and toks[i - 2].lower() in POSSESSIVES
                    and toks[i - 1].isalpha() and toks[i - 1].islower()
                    and toks[i - 1] not in stop):
                hold_name(i, _name_strength(tok, cued=False) and "weak")
            # A place of hers: "in our den", "at my cabin", "from our
            # basement" (so "old den" does not go out from "the old computer
            # in our den in Bakersfield"). Held until she
            # ticks it; the subject still keeps it.
            if (base in ("my", "our") and prev in ("in", "at", "inside", "outside")
                    and i + 1 < len(toks) and not _CLAUSE_BREAK_RE.search(seps[i + 1])):
                for k in range(i + 1, min(len(toks), i + 3)):
                    if k > i + 1 and _CLAUSE_BREAK_RE.search(seps[k]):
                        break
                    b = _base(toks[k])
                    if (not b or not b.isalpha() or b in FUNCTION_WORDS or b in stop
                            or b in BOT_NOUNS or is_hard_word(b) or _kin(b)
                            or _forms(b) & (AUDIENCE | BEINGS | TIME_WORDS)):
                        break
                    if b in SUBJECT_FILLER or b in ("own", "new", "old", "little", "big",
                                                    "small", "spare", "free"):
                        continue
                    if _forms(b) & _HOME_PLACES:
                        marks.mark_held(toks[k], "place", True)
                    break
            # A place: a capitalised word or a compass region after a place
            # cue ("in Tucson", "to Mexico City", "in the Pacific Northwest").
            cued = prev in PLACE_CUES or (prev == "the" and i > 1
                                          and toks[i - 2].lower() in PLACE_CUES)
            if i > 0 and cued:
                if ((_is_cap(tok) or _forms(base) & REGION_WORDS)
                        and not is_hard_word(base)
                        and base not in LANGUAGES and not_filler(base)):
                    j = i
                    while (j < len(toks) and toks[j] != "&"
                           and (_is_cap(toks[j]) or _forms(toks[j]) & REGION_WORDS)
                           and not is_hard_word(toks[j])):
                        forms = _forms(toks[j])
                        why_place = "region" if forms & REGION_WORDS else "place"
                        marks.mark_held(toks[j], why_place, why_place == "place")
                        places |= forms
                        j += 1
                    continue
            # Other capitalised words: names, businesses, brands.
            if not _is_cap(tok) or base in ("i", "i'm", "i'll", "i've", "i'd"):
                continue
            if base in LANGUAGES or (len(tok) > 1 and re.sub(r"s$", "", tok).isupper()):
                continue  # a language, or an acronym (NPCs, ISS, D&D)
            if (base in BOT_NOUNS and i > 0 and not line_start and _is_cap(toks[i - 1])
                    and (marks.held.get(_base(toks[i - 1])) or ("",))[0] in ("name", "brand")):
                # "Home Assistant", "Google Assistant": the rest of a held
                # name, held with it, so no phrase keeps half of the name
                # ("assistant dashboard").
                marks.mark_held(base, (marks.held.get(_base(toks[i - 1])))[0], True)
                continue
            if is_hard_word(base) or not not_filler(base) or base in BOT_NOUNS:
                continue
            kind = dict_kind(base)
            mid = not line_start
            if kind == "proper":
                marks.mark_held(base, "name", True)
            elif mid and kind == "unknown":
                marks.mark_held(base, "brand", True)
            elif kind == "unknown":
                # A capitalised word the spelling list does not know, at the
                # start of a sentence or a line ("Liam needs a chores bot.",
                # "Kids:\nLiam\nNoah"), is a name until she ticks it (so
                # "liam chores" does not go out). It still names
                # the bot when it sits in the subject ("Minecraft server bot").
                marks.mark_held(base, "name", True)
            elif mid and "&" not in (toks[i - 1], toks[i + 1] if i + 1 < len(toks) else ""):
                # An ordinary word written with a capital mid-sentence: a
                # name ("a soccer bot for Max"), part of one ("Sweet Rise")
                # or a product ("Google Calendar"). Held; it still stays in
                # the subject when it sits there ("a Soccer coach bot"). A
                # title joined by "&" ("Dungeons & Dragons") is a product.
                marks.mark_held(base, "name", True)
    # Hard words inside joined words too ("sister-in-law", "my 10-year-old").
    for w in goal_raw:
        why = is_hard_word(w)
        if why and w not in ("i", "s", "m"):
            marks.mark_hard(w, why)
    # Days of the year of more than one word ("Memorial Day", "New Year's"):
    # the run is held as a date, and (so "day", "new", "good" and "break"
    # do not go out alone) every word of it
    # that is not a joining word.
    for seq, _own in date_phrase_hits(goal_raw):
        if seq not in marks.date_seqs:
            marks.date_seqs.append(seq)
        for w in seq:
            if w not in FUNCTION_WORDS and len(w) > 1 and not is_hard_word(w):
                marks.mark_date(w)
    # Word by word: hyphens, slashes, digits, glued forms and a stretch of
    # time after a season or holiday.
    for k in sorted(_raw_time_words(goal_raw)):
        w = goal_raw[k]
        if not is_hard_word(w):
            marks.mark_date(w)
        for x in re.findall(r"[a-z]+", w):
            if x != w and len(x) > 2 and not is_hard_word(x):
                marks.mark_date(x)
    # The season or holiday inside a glued word is held on its own too
    # ("summercamp": "summer", "Christmastime": "christmas", "wintertrip":
    # "winter"; otherwise the whole glued word is held but its time piece
    # is sendable).
    for w in sorted(marks.dates):
        for t in _GLUE_PIECES:
            if w != t and len(w) > len(t) and (w.startswith(t) or w.endswith(t)):
                marks.mark_date(t)
        # Every piece of a glued time ("springbreaktrip": "spring", "break",
        # "trip").
        for t in (glued_time_pieces(w) if w.isalpha() and not is_common_word(w) else []):
            if len(t) > 2 and not is_hard_word(t):
                marks.mark_date(t)
    # "mid-July", "mid summer": the "mid" of a time is part of the time.
    for k, w in enumerate(goal_raw[:-1]):
        nxt = goal_raw[k + 1]
        if w == "mid" and (_forms(nxt) & (MONTHS | SEASONS | PLAIN_SEASONS | HOLIDAYS)
                           or nxt in marks.dates):
            marks.mark_date(w)
    in_dates = _date_positions(goal_raw)
    # Health and diet words, said about someone or not. "gluten free" and
    # "nut-free" hold the "free" too: the detail is the whole phrase.
    from puffpai.security.data_classifier import health_terms
    for term in health_terms(text):
        for w in goal_words.raw_words(term):
            if len(w) > 1:   # not the "s" of "Alzheimer's"
                marks.mark_held(w, "health", False)
    for k, w in enumerate(goal_raw):
        if _forms(w) & DIET_WORDS:
            marks.mark_held(w, "health", False)
        elif w == "free" and k and _forms(goal_raw[k - 1]) & DIET_WORDS:
            marks.mark_held(w, "health", False)
    # Sensitive subjects: mental health, addiction, sex life and identity,
    # belief, legal trouble, money hardship, violence, grief. Never
    # tickable; searched only when they name the bot itself.
    for k, w in enumerate(goal_raw):
        if k in in_dates:
            continue            # "memorial" of "Memorial Day" is a date
        why_s = sensitive_why(w, goal_raw[k - 1] if k else "")
        if why_s:
            marks.mark_held(w, why_s, False)
    # Words private only in a goal about her or her people ("for my shingles
    # stuff", "plan around lent with my mom"), never next to a word that
    # makes them an everyday thing ("asphalt shingles"). See
    # PERSONAL_SENSITIVE.
    # Her people's news ("my brother just got engaged"): held and listed
    # as kept private, never ticked. Her own news ("we just moved") and the
    # words elsewhere ("a retirement planner bot") stay ordinary words.
    for craw in _clause_words(text):
        for k, w in enumerate(craw):
            if w not in LIFE_EVENTS or w in ("getting", "opening"):
                continue
            who = _their_news_at(craw, k)
            if who >= 0:
                marks.mark_held(w, "private", False)
                if not is_hard_word(craw[who]) and craw[who] not in _NEWS_PERSONS:
                    marks.mark_held(craw[who], "private", False)
        # "my late aunt": one of her people died. "late" is held for good,
        # and so is what a model would say about it ("estate sale"; quiet:
        # never listed, she did not type it).
        for k in range(len(craw)):
            if _death_of_hers(craw, k):
                marks.mark_held("late", "private", False)
                marks.quiet |= _AFTER_A_DEATH
            # "our tenth anniversary", "his fortieth birthday", "our golden
            # anniversary": how many years, a detail of her people's lives.
            if ((craw[k] in _ORDINALS or craw[k] in _ANNIVERSARY_NAMES)
                    and k + 1 < len(craw) and craw[k + 1] in _COUNTED_DAYS):
                marks.mark_held(craw[k], "private", False)
                if k and craw[k - 1] in ("twenty", "thirty", "forty", "fifty", "sixty",
                                         "seventy", "eighty", "ninety"):
                    marks.mark_held(craw[k - 1], "private", False)
        # "pregnant with my second baby", "our first baby": which of her
        # babies it is, hers too (so "second baby" is not sendable for a
        # pregnancy goal).
        for k in range(1, len(craw) - 1):
            if (craw[k] in _BIRTH_ORDER and craw[k - 1] in POSSESSIVES
                    and craw[k + 1] in ("baby", "babies", "child", "kid", "grandchild",
                                        "grandbaby", "pregnancy")):
                marks.mark_held(craw[k], "private", False)
        # "my granddaughter is having her first baby": which baby it is
        # ("first") is her people's news and is held; "baby" stays a plain
        # word ("baby blanket pattern").
        for k in range(len(craw) - 1):
            if craw[k] not in ("having", "expecting"):
                continue
            j = k + 1
            if j < len(craw) and (craw[j] in POSSESSIVES or craw[j] in ("a", "an", "the")):
                j += 1
            if (j + 1 < len(craw) and craw[j] in _BIRTH_ORDER
                    and craw[j + 1] in ("baby", "babies", "child", "kid", "grandchild",
                                        "grandbaby")):
                held_name = (lambda x: (marks.held.get(x) or ("",))[0] == "name")
                if any(_kin(x) or x in _NEWS_PERSONS or held_name(x) for x in craw[:k]):
                    marks.mark_held(craw[j], "private", False)
    if _goal_is_personal(goal_raw):
        date_words = {goal_raw[k] for k in in_dates}
        for w, cat in personal_sensitive_words(text):
            if w not in date_words:
                marks.mark_held(w, "health" if cat == "health" else "private", False)
        for k, w in enumerate(goal_raw):
            # "chapter 7", "section 8", "stage 4" in a goal about her or her
            # people, wherever they sit ("plan around chapter 7 with my
            # mom"); the number is a hard word already.
            numbers, cat = _NUMBERED_PRIVATE.get(w, ((), ""))
            if cat and k + 1 < len(goal_raw) and goal_raw[k + 1] in numbers:
                marks.mark_held(w, "health" if cat == "health" else "private", False)
    for seq, cat in sensitive_phrase_hits(goal_raw):
        # A pair is one detail ("green card", not "green" and "card" each),
        # listed as the pair.
        # Its joining words are not the detail ("had a stroke", "in memory
        # of"); the screen holds the pair in any phrase either way.
        # A particle of an idiom stays ("coming out", "laid off").
        kept = [w for w in seq if (w not in FUNCTION_WORDS or (w in _PARTICLES and w != seq[0]))
                and w not in ("had", "has", "have") and not is_hard_word(w)]
        if not kept:
            continue
        why_p = "health" if cat == "health" else "private"
        if len(kept) == 1:
            marks.mark_held(kept[0], why_p, False)
        else:
            marks.mark_held(" ".join(kept), why_p, False)
    # What she or her people are going through, read from the sentence's
    # shape, whatever the word is (see _story_holds). Never tickable.
    for w in _story_holds(text):
        marks.mark_held(w, "health" if sensitive_category(w) == "health" else "private",
                        False)
    # Her home town and state, where the goal says them. Never allowed out.
    singles, seqs = home_words(home)
    home_here = False
    for w in goal_raw:
        if _forms(w) & singles:
            marks.mark_held(w, "place", False)
            places |= _forms(w)
            home_here = True
    for seq in seqs:
        if contains_seq(goal_raw, seq):
            home_here = True
            for w in seq:
                marks.mark_held(w, "place", False)
                places |= _forms(w)
    holds_place = bool(places) or home_here
    if holds_place:
        for w in goal_raw:
            if _forms(w) & REGION_WORDS:
                marks.mark_held(w, "region", False)
        for seq in REGION_PHRASES:
            if contains_seq(goal_raw, seq):
                for w in seq:
                    marks.mark_held(w, "region", False)
    return marks, holds_place, places


def goal_holds_place(goal: str, *, home: dict | None = None) -> bool:
    """Does the goal name a place (a place cue, or her home town or state)?"""
    return _goal_marks(goal, home)[1]


# --- the subject ------------------------------------------------------------------

#: Words for someone other than her ("he", "her", "their").
_OTHER_PEOPLE = frozenset("""he she him her his hers they them their theirs he's she's they're
himself herself themselves""".split())


def _other_person_in(toks: list[str], lows: list[str], marks: "_Marks") -> bool:
    """Does this sentence name someone other than her: one of her people
    ("my husband", "my kids", "my son's"), "he"/"she"/"her"/"their", or a
    name the goal holds ("for Max")? Pets are not people here."""
    for k, low in enumerate(lows):
        if not low:
            continue
        if low in _OTHER_PEOPLE:
            return True
        if _kin(low):
            # "for single parents", "a bot for moms": people in general, an
            # audience, unless they are hers ("my parents").
            if goal_words.singular(low) != low and not _owned_here(toks, k):
                continue
            return True
        if _forms(low) & (AUDIENCE | _HUMAN_BEINGS) and _owned(toks, k):
            return True
        held = marks.held.get(low)
        if held and held[0] == "name":
            return True
    return False


def _subject(goal: str, home: dict | None) -> tuple[str, list[str]]:
    goal = _fold(goal)
    marks, _, places = _goal_marks(goal, home)
    subject, notes, refused = _bot_noun_subject(goal, marks, places)
    if subject or refused:
        return subject, notes
    return _product_subject(goal, marks)


#: Words in front of a maker's name that make "<Maker> <thing>" a thing she
#: has or wants ("his own Minecraft server", "a used Ender 3 printer"). Never
#: "my"/"our": "my Liam soccer games" reads the same as "my Sonos speakers".
_PRODUCT_BEFORE = frozenset("a an the own new old used this that some".split())
#: Machines, devices and programs: the only things a maker's name may name
#: as a subject ("Minecraft server", "Ender 3 printer"), never a team, a
#: party or a lesson ("the Kenji soccer team" holds "Kenji").
_MACHINE_NOUNS = frozenset("""server servers printer printers console consoles laptop laptops
computer computers pc pcs phone phones tablet tablets speaker speakers light lights lamp lamps
bulb bulbs camera cameras cam cams thermostat thermostats router routers hub hubs vacuum
vacuums watch watches tv tvs television televisions drone drones robot robots mower mowers
doorbell doorbells lock locks plug plugs sensor sensors display displays monitor monitors
headphones earbuds keyboard keyboards controller controllers game games mod mods launcher
software program programs device devices machine machines oven ovens fridge fridges washer
washers dishwasher dishwashers reader readers kindle telescope telescopes microscope
microscopes guitar guitars piano pianos keyboard synth synths amp amps pedal pedals
headset headsets router modem modems nas car cars truck trucks bike bikes scooter scooters
""".split())


#: Words in front of "the"/"a" that make the next capital someone's name
#: ("for the Kenji team", "with the Liam games").
_PERSON_CUES_BEFORE = frozenset("for with to from by at about".split())


def _product_subject(goal: str, marks: "_Marks") -> tuple[str, list[str]]:
    """The subject of a goal with no "<kind> bot" in it: the first thing
    she names by its maker, "<Maker> <thing>" after "a", "the", "own",
    "used" or "new" ("his own Minecraft server": "minecraft server"; "a used
    Ender 3 printer": "ender printer"). The maker must be a word the goal
    holds as a brand she may tick (never a name a cue gave), and the thing
    an ordinary lower-case word that is not a person, a time, a number or a
    sensitive word. ``("", [])`` when there is none.

    The subject rule applies to every held class (otherwise a Minecraft goal
    holds "minecraft" as a brand although the bot is about it)."""
    for off, sent in _spans(goal, _SENT_RE):
        toks, seps, starts = _tok_seps(sent, off)
        n = len(toks)
        for i in range(1, n):
            tok = toks[i]
            if "\n" in seps[i] or "\r" in seps[i] or _CLAUSE_BREAK_RE.search(seps[i]):
                continue
            base = _base(tok)
            held = marks.held.get(base)
            # "set up Home Assistant", "put Home Assistant on a box": a
            # product whose name ends in a bot noun is what the bot is about
            # (not "put home").
            if (i + 1 < n and _is_cap(tok) and _is_cap(toks[i + 1])
                    and _base(toks[i + 1]) in ("assistant", "assistants")
                    and held and held[1] and held[0] in ("name", "brand")
                    and starts[i] not in marks.strong and not (_forms(base) & marks.person)
                    and not _CLAUSE_BREAK_RE.search(seps[i + 1])
                    and _base(toks[i - 1]) not in _PERSON_CUES_BEFORE):
                words = [re.sub(r"[^a-z0-9]", "", base), "assistant"]
                if words[0]:
                    return " ".join(words), ["brand"]
            if not (_is_cap(tok) and held and held == ("brand", True)) \
                    or starts[i] in marks.strong \
                    or tok.lower().replace("\u2019", "'").endswith("'s") \
                    or _base(toks[i - 1]) not in _PRODUCT_BEFORE \
                    or (i >= 2 and _base(toks[i - 2]) in _PERSON_CUES_BEFORE):
                continue
            j = i + 1
            if j < n and toks[j].isdigit() and not _CLAUSE_BREAK_RE.search(seps[j]):
                j += 1                          # "Ender 3 printer"
            if j >= n or _CLAUSE_BREAK_RE.search(seps[j]) or "\n" in seps[j]:
                continue
            thing = toks[j]
            nb = _base(thing)
            if not (thing.isalpha() and thing.islower() and dict_kind(nb) == "common"
                    and nb not in _ALSO_NAMES and nb not in FUNCTION_WORDS
                    and nb not in PRONOUNS and nb not in BOT_NOUNS and not _kin(nb)
                    and not (_forms(nb) & (AUDIENCE | BEINGS | TIME_WORDS))
                    and not is_hard_word(nb) and not sensitive_category(nb)
                    and not marks.held.get(nb) and _forms(nb) & _MACHINE_NOUNS):
                continue
            words = [re.sub(r"[^a-z0-9]", "", base), nb]
            if all(words):
                return " ".join(words), ["brand"]
    return "", []


def _bot_noun_subject(goal: str, marks: "_Marks", places: set[str]
                      ) -> tuple[str, list[str], bool]:
    """The subject read from the first sentence with a bot noun (see
    :func:`subject_of`): ``(subject, notes, refused)``. *refused*: the
    sentence named a sensitive subject that may not be searched (see below);
    then no other reading is tried."""
    for off, sent in _spans(goal, _SENT_RE):
        toks, seps_, starts = _tok_seps(sent, off)
        lows = [_base(t) for t in toks]
        # "Home Assistant", "Google Assistant": a product's name, not the
        # kind of bot ("put home" is not the subject of "My husband Dev put
        # Home Assistant on a little box").
        idx = [i for i, w in enumerate(lows) if w in BOT_NOUNS
               and not (w in ("assistant", "assistants") and i > 0 and _is_cap(toks[i])
                        and _is_cap(toks[i - 1]) and lows[i - 1] not in ("a", "an", "the",
                                                                         "my", "our"))]
        if not idx:
            continue
        last = idx[-1]
        # What she adds after a label ("Context: sober coach.") is about
        # her, never the kind of bot ("coach" does not make "context sober"
        # the subject).
        if any(_label_at(lows, seps_, x) for x in range(last)):
            continue
        window: list[int] = []
        j = last - 1
        while j >= 0 and len(window) < 4:
            if lows[j] in SUBJECT_STOPS:
                break
            window.insert(0, j)
            j -= 1
        if window and lows[window[-1]] in GENERIC_BOT_WORDS:
            window.pop()
        # A subject that is hers ("our ...
        # party bot", "my son's summer camp bot") holds her own plan's time,
        # not a kind of bot, so its season or holiday words leave the
        # subject ("Plan our Cinco de Mayo party bot" does not send "cinco"
        # and "mayo"). "a Christmas cookie bot" is still a kind of bot.
        owned_subject = any(lows[x] in POSSESSIVES for x in window)
        start = window[0] if window else last
        for x in range(start - 1, max(-1, start - 4), -1):
            if owned_subject:
                break
            raw_x = toks[x].lower().replace("’", "'")
            if lows[x] in POSSESSIVES or (raw_x.endswith("'s") and (
                    _kin(lows[x]) or _forms(lows[x]) & (AUDIENCE | BEINGS))):
                owned_subject = True
                break
            if lows[x] in FUNCTION_WORDS or lows[x] in BOT_NOUNS:
                break
        # A day of the year that holds a month or a weekday ("Black Friday",
        # "May Day") leaves the subject whole, never cut to "black deal
        # finder" or "day festival planner".
        cut: set[int] = set()
        singles = [goal_words.singular(x) for x in lows]
        for seq, _own in DATE_PHRASES:
            target = [goal_words.singular(x) for x in seq]
            for a in range(len(lows) - len(seq) + 1):
                run = range(a, a + len(seq))
                if singles[a:a + len(seq)] == target and any(
                        is_hard_word(lows[x]) or toks[x] == "May" for x in run):
                    cut |= set(run)
        kept: list[tuple[str, str]] = []   # (as typed, lower)
        for k in window:
            tok, low = toks[k], lows[k]
            if k in cut:
                continue
            if tok == "&":
                kept.append((tok, "&"))
                continue
            if low in SUBJECT_FILLER:
                continue
            if owned_subject and _forms(low) & marks.dates:
                continue
            if (low in marks.hard or _forms(low) & set(marks.hard)) \
                    and not (_forms(low) & marks.dates):
                # A digit-letter word ("3d") is the thing itself, not a number.
                # A season or holiday in the subject is what the bot is
                # about ("a summer camp finder bot"), so it stays.
                if not (_has_digit(low) and not low.isdigit()
                        and not _AGE_RE.fullmatch(low)
                        and marks.hard.get(low) == "number"):
                    continue
            held = marks.held.get(low) or marks.held.get(goal_words.singular(low))
            # A place always goes; a name only where a cue gave it ("my son
            # Will", "named Biscuit", "Jake's"): a proper noun that names the
            # kind of bot ("a Portland bakery bot", "Minecraft") stays.
            if held and held[0] in ("place", "region"):
                continue
            if starts[k] in marks.strong or _forms(low) & marks.person:
                continue
            if _forms(low) & places:
                continue
            kept.append((tok, low))
        # Tidy the edges: "&" and "and"/"or" never start or end the subject.
        while kept and kept[0][1] in ("&", "and", "or"):
            kept.pop(0)
        while kept and kept[-1][1] in ("&", "and", "or"):
            kept.pop()
        words: list[str] = []
        for tok, low in kept:
            clean = re.sub(r"[^a-z0-9&]", "", low)
            clean = clean.replace("&", " and ")
            words.extend(clean.split())
        words = words[-4:]
        notes: list[str] = []
        prev_low = ""
        #: A sensitive subject word that is not a diet word ("vegan meal
        #: planner" keeps its note and is searched as before).
        story_subject = False
        for tok, low in kept:
            if low == "&":
                continue
            cap_unknown = (_is_cap(tok) and dict_kind(low) == "unknown"
                           and not (len(tok) > 1 and re.sub(r"s$", "", tok).isupper())
                           and not _has_digit(low))
            # A health word is a health detail, not a brand, even when the
            # list spells it with a capital ("Alzheimer's", "Crohn's"). The
            # other sensitive subjects (belief, legal trouble, money
            # hardship, grief, ...) get the "private" note.
            touchy = sensitive_why(tok, prev_low)
            # A word this goal holds as a health or private detail where it
            # sits ("a cutting tracker bot for my daughter": "cutting" is
            # held, PERSONAL_SENSITIVE) is a sensitive subject word too.
            held_here = marks.held.get(low) or marks.held.get(goal_words.singular(low))
            if not touchy and held_here and held_here[0] in ("health", "private") \
                    and not held_here[1]:
                touchy = held_here[0]
            if touchy and not (_forms(low) & DIET_WORDS
                               or (low == "free" and prev_low and _forms(prev_low) & DIET_WORDS)):
                story_subject = True
            prev_low = low
            if touchy:
                # Every sensitive subject carries the pinned "health" note
                # (notes are a subset of brand/health), so the screen always
                # shows the plain note and the "Only look on this computer"
                # choice, "a divorce planner" too.
                if "health" not in notes:
                    notes.append("health")
            elif low not in LANGUAGES and (dict_kind(low) == "proper" or cap_unknown):
                if "brand" not in notes:
                    notes.append("brand")
        if sensitive_phrase_hits(words):
            story_subject = True
            if "health" not in notes:
                notes.append("health")
        # "a simple helper bot for our home because of my dating app" must
        # not search "dating": a sensitive subject
        # read in front of a later bot noun, when the sentence named the bot
        # earlier ("helper bot ... my dating app"), may be her own thing, not
        # the bot. Fail closed: no subject, so nothing sensitive is sent.
        # So is one read across one of her people doing something ("my wife
        # deals with dating app": "wife" is in the window). Whose it is ("my
        # son's autism therapy bot", "her anxiety journal bot") is fine.
        first = window[0] if window else last
        crossed = any(
            not toks[k].lower().replace("’", "'").endswith("'s")
            and ((_kin(lows[k])) or (lows[k] in PRONOUNS and lows[k] not in POSSESSIVES))
            for k in window)
        if "health" in notes and (
                any(lows[k] in ("bot", "bots") for k in idx if k < first) or crossed):
            return "", [], True
        # A subject word is sendable only when no person or story of one is
        # attached ("a stroke recovery exercise bot for my husband" must not
        # send "stroke"). A sensitive subject in a sentence that
        # names one of her people ("for my husband", "her anxiety journal
        # bot", "my son's autism therapy bot") is no subject: nothing
        # sensitive is sent. Her own ("a sobriety tracker bot for me") is
        # searched with its plain note, as before.
        if story_subject and _other_person_in(toks, lows, marks):
            return "", [], True
        return " ".join(words), [n for n in SUBJECT_NOTES if n in notes], False
    return "", [], False


#: The notes a subject can carry, in the order they are shown (pinned, design
#: I-1). Every sensitive subject gets ``health``; its exact category is
#: :func:`sensitive_category` of the subject words.
SUBJECT_NOTES = ("brand", "health")


def subject_of(goal: str) -> str:
    """The kind of bot the goal asks for ("wedding planner"), or ``""``.

    One pinned algorithm:

    1. In the first sentence with a bot noun (bot, app, helper, buddy,
       assistant, tutor, coach), take the **last** bot noun and up to four
       words in front of it, stopping after the nearest a/an/my/the/build/
       make/want/need.
    2. A generic word (helper, assistant, buddy, app) right in front of that
       bot noun goes; so do articles, filler, every hard word (relatives,
       pronouns, names given by a cue, ages, numbers except digit-letter
       words like "3d", dates) and every place the goal marks.
    3. Lower case, only ``[a-z0-9 &]``, ``&`` becomes ``and``, at most four
       words.
    """
    return _subject(goal, None)[0]


#: What the owner reads after a sensitive subject that is not a health
#: word ("Searching with: divorce planner" + this). The pinned note stays
#: ``health`` for every sensitive subject so the wizard always warns; a
#: wizard that shows this line for :func:`subject_note_kind` ``private``
#: says it truthfully ("a health word" is wrong for legal, money and belief
#: subjects).
PRIVATE_SUBJECT_NOTE = (' (a private subject; choose "Only look on this computer" to '
                        'keep it here)')


def subject_note_kind(goal: str, *, home: dict | None = None) -> str:
    """Which plain note a sensitive subject needs: ``health`` (health,
    mental health, addiction, diet), ``private`` (sex life and identity,
    belief, legal trouble, money hardship, violence, grief) or ``""`` when
    the subject is not sensitive. Health wins when the subject has both."""
    goal = _fold(goal)
    subject = _bot_subject(goal, home)[0]
    if not subject:
        return ""
    words = subject.split()
    cats = [sensitive_category(w, words[k - 1] if k else "") for k, w in enumerate(words)]
    cats += [c for _seq, c in sensitive_phrase_hits(words)]
    cats = [c for c in cats if c]
    if not cats:
        return ""
    return "health" if "health" in cats else "private"


def without_hard_tokens(text: str) -> str:
    """*text* with every hard word taken out whole, joined words included.

    "a homework helper for my 10-year-old's class" gives "a homework helper
    for class": the pieces of an age ("year", "old") never survive as words.
    """
    def cut(m: "re.Match[str]") -> str:
        return " " if is_hard_word(m.group(0)) else m.group(0)
    return _TOKEN_RE.sub(cut, _fold(text).replace("\u2019", "'"))


#: What ends a run of words for the neighbour rule: a word after one of
#: these is not the sensitive word's own ("my anxiety, and a garden").
_CLAUSE_BREAK_RE = re.compile(r"[,.;:!?()\[\]/\"“”]")


def _sensitive_at(lows: list[str], i: int) -> str:
    """The sensitive category of the word at *i* ("xanax", "self-harm", the
    "harm" of "self harm"), ``""`` when it has none."""
    cat = sensitive_category(lows[i], lows[i - 1] if i else "")
    if cat:
        return cat
    here = goal_words.raw_words(" ".join(lows[max(0, i - 3):i + 1]))
    before = goal_words.raw_words(" ".join(lows[max(0, i - 3):i]))
    old = {seq for seq, _c in sensitive_phrase_hits(before)}
    return next((c for seq, c in sensitive_phrase_hits(here) if seq not in old), "")


#: How often or when, never part of the story itself ("my husband hits me
#: sometimes").
_TIME_ADVERBS = frozenset("""sometimes often always usually lately recently again still now
today tonight daily weekly nightly monthly yearly also too even ever never just really
very much more less already yet soon later occasionally constantly frequently regularly
anymore anyway last next this year years month months week weeks weekend weekends""".split())


def _sensitive_neighbours(text: str, subject_forms: set[str]) -> list[tuple[str, str]]:
    """Words the goal ties to a sensitive word by writing them right after it:
    "self-harm urges", "custody hearing", "xanax doses", "parole meeting".

    With only the sensitive word held, its neighbour alone still tells the
    story once a search phrase is made from the rest of her sentence
    ("journal urge"). Such a neighbour is held
    with the same reason and can never be ticked. Not when the sensitive
    word names the bot ("a sobriety tracker bot"): the subject is searched
    with its plain note.
    """
    out: list[tuple[str, str]] = []
    for off, sent in _spans(text, _SENT_RE):
        toks, seps, _ = _tok_seps(sent, off)
        lows = [_base(t) for t in toks]
        for i in range(len(toks) - 1):
            cat = _sensitive_at(lows, i)
            # "church group", "therapy schedule": a listed word with an
            # everyday meaning too (STORY_EXEMPT, which holds AMBIGUOUS) does
            # not hold its
            # neighbour.
            if not cat or _forms(lows[i]) & subject_forms or _forms(lows[i]) & STORY_EXEMPT:
                continue
            j = i + 1
            nxt = lows[j]
            if _CLAUSE_BREAK_RE.search(seps[j]) or not nxt or _has_digit(nxt):
                continue
            if (nxt in FUNCTION_WORDS or nxt in PRONOUNS or nxt in goal_words.stopwords()
                    or nxt in _TIME_ADVERBS
                    or is_hard_word(nxt) or _forms(nxt) & subject_forms
                    or sensitive_category(nxt, lows[i])):
                continue
            # A plain verb that starts her job ("my grandpa with alzheimers
            # find old baseball games") is what she wants done, not part of
            # the detail.
            # After "<her person> with <condition>" a plain verb starts the
            # job even when another verb-like word follows ("my grandma with
            # diabetes bake low sugar cookies"). Elsewhere the stricter
            # reading stays, so "insulin pump schedule" still holds "pump".
            with_person = (i >= 2 and lows[i - 1] == "with"
                           and not _CLAUSE_BREAK_RE.search(seps[i])
                           and not toks[i - 2].lower().endswith("'s")
                           and (_kin(toks[i - 2]) or (lows[i - 2] in _WITH_PEOPLE
                                                      and _owned(toks, i - 2))))
            if _job_verb_at(toks, seps, j, after_condition=with_person):
                continue
            why = "health" if cat == "health" else "private"
            for part in [x for x in re.split(r"[-']", nxt) if len(x) > 1] or [nxt]:
                out.append((part, why))
        # The word in front, too ("cystic" of "cystic fibrosis", "premature"
        # of "premature ejaculation", "sickle" of "sickle cell anemia").
        for i in range(1, len(toks)):
            cat = _sensitive_at(lows, i)
            if not cat or _forms(lows[i]) & subject_forms or _forms(lows[i]) & STORY_EXEMPT:
                continue
            j = i - 1
            prv = lows[j]
            raw_prev = toks[j].lower().replace("’", "'")
            if _CLAUSE_BREAK_RE.search(seps[i]) or not prv or _has_digit(prv):
                continue
            if (prv in FUNCTION_WORDS or prv in PRONOUNS or prv in goal_words.stopwords()
                    or prv in _TIME_ADVERBS or prv in _ARTICLES or prv in BOT_NOUNS
                    or raw_prev.endswith("'s") or is_hard_word(prv)
                    or _forms(prv) & subject_forms or sensitive_category(prv)
                    or _forms(prv) & (AUDIENCE | BEINGS | RELATIVES)):
                continue
            why = "health" if cat == "health" else "private"
            for part in [x for x in re.split(r"[-']", prv) if len(x) > 1] or [prv]:
                out.append((part, why))
    return out


def _listed_category(word: str, prev: str = "") -> str:
    """The category of *word* in this module's own sensitive lists
    (:data:`SENSITIVE_WORDS`), ``""`` when it is not there. The data
    classifier's wider health list and the diet list are left out on
    purpose: a pet's medicine or a gluten-free meal plan holds its own words
    but does not make the whole goal private."""
    w = _base(word)
    if not w or _ordinary_here(w, prev):
        return ""
    cat = listed_lookup(w)
    if cat:
        return cat
    parts = [x for x in re.split(r"[-']", w) if len(x) > 1]
    if len(parts) > 1:
        for part in parts:
            cat = _listed_category(part)
            if cat:
                return cat
    if "-" in w and w.replace("-", "") != w:
        return _listed_category(w.replace("-", ""))
    return ""


#: Listed sensitive words that are everyday words in ordinary goals ("drug
#: names" for a nursing student, "physical therapy" for a fitness coach,
#: "track our income", "death saves" in a game, "soil fertility", "church
#: bake sale"). Each is still held on its own and never searched unless it
#: names the bot; it only does not make the whole goal private.
STORY_EXEMPT = frozenset("""drug drugs alcohol alcohols drinking drunk booze liquor nicotine
hormone hormones testosterone estrogen stimulant stimulants income incomes salary salaries
paycheck paychecks wage wages payday church churches chapel parish congregation bible
bibles prayer prayers pray praying worship sermon sermons faith god spiritual spirituality
death deaths dying violence violent therapy therapies therapist counseling counselling
counselor counsellor fertility gospel hymn hymns psalm psalms""".split())


#: Held words with an everyday meaning too ("AA batteries", "bailing water",
#: "a sobering thought", "an arresting view"). Each is held on
#: its own, but neither makes the whole goal private nor holds the word
#: after it.
AMBIGUOUS = frozenset("""aa sobering arresting escorted escorting bailed bailing graved
graving graveness kinked kinking murder murders victim victims disabled prosthetic
prosthetics paralysis mania plea visitation confession confessions orphan orphans monk
monks salvation resurrection punched bully bullies surrogate surrogates adoption adoptions
loans cheated groomed stoma dispensary surgery surgeries vertigo erection
erections stutter stuttering stammer stammering lube pacemaker ventilator cataract cataracts
junkie junkies paranoid inpatient outpatient tarot restraining bypass warrant warrants
separation disorder disorders disease diseases deficiency deficiencies toxicity murmur
astrology""".split())
#: The everyday-meaning words only ("drug names", "track our income"), not
#: the ambiguous ones ("AA batteries"): only these can be a word of her job.
_EVERYDAY_SENSITIVE = STORY_EXEMPT
STORY_EXEMPT = STORY_EXEMPT | AMBIGUOUS


#: Words of a subject that make the bot about money ("a family budget bot").
_MONEY_HEADS = frozenset("""budget budgets budgeting finance finances financial money spending
expense expenses saving savings bill bills banking bookkeeping""".split())


def subject_domain_words(goal: str, subject: str) -> set[str]:
    """Money words that are the bot's own subject although not in its name:
    in a bot about money (the subject holds "budget", "finances", ...), a
    word of the money list ("debt", "overdraft") said as her own job ("pay
    off $9,000 of credit card debt") counts as a subject word. Never when
    the clause names someone else ("my brother's debt", "he has debt"), when
    the word is owned ("our debt", "my debt") or is what a person is going
    through ("we are in debt", "I have debt"), or when a story shape holds
    it. Returns the word forms.

    The subject rule applies to every held class (otherwise a family budget
    goal holds most of its phrases because "debt" makes the whole goal
    private)."""
    words = set(str(subject or "").split())
    if not words & _MONEY_HEADS:
        return set()
    text = _fold(goal)
    story = set(_story_holds(text))
    out: set[str] = set()
    for raw in _clause_words(text):
        other = any(w in _OTHER_PEOPLE or _kin(w) for w in raw)
        for k, w in enumerate(raw):
            if _listed_category(w, raw[k - 1] if k else "") != "money" or w in story:
                continue
            if other or _kin_story_at(raw, k) or any(x in POSSESSIVES for x in raw[max(0, k - 3):k]):
                continue
            out |= _forms(w)
    return out


#: Health words that are one subject said another way ("pregnant" in her
#: goal, "pregnancy" in a model's phrase). A subject word holds its family.
_TOPIC_FAMILIES = (
    frozenset("pregnant pregnancy pregnancies prenatal maternity".split()),
    frozenset("diabetes diabetic diabetics".split()),
    frozenset("medicine medicines medication medications med meds medical".split()),
    frozenset("drug drugs".split()),
    frozenset("vegan vegans veganism".split()),
    frozenset("vegetarian vegetarians vegetarianism".split()),
)
#: Animals a bot can be about: their health words are not a person's.
_ANIMAL_WORDS = frozenset(w for w in _PLANT_ANIMAL if goal_words.singular(w) in {
    "bee", "bird", "cat", "cattle", "chicken", "dog", "fish", "goat", "horse", "livestock",
    "pet", "poultry"}) | frozenset("puppy puppies kitten kittens pony ponies vet vets "
                                   "rabbit rabbits hamster hamsters parrot parrots".split())
#: People words that make an item about someone other than her.
_ITEM_PEOPLE = frozenset("he she him they them he's she's they're himself herself "
                         "themselves".split())
_ITEM_THEIRS = frozenset("his her hers their theirs".split())
#: Sensitive categories that never become her bot's subject through her
#: job items when she names no kind of bot (a story of violence, belief,
#: legal trouble, sex life, grief): there every word stays held.
_STORY_ONLY_CATS = frozenset("violence belief legal sex grief".split())


def _family(word: str) -> set[str]:
    """*word*'s forms and its subject family (see :data:`_TOPIC_FAMILIES`)."""
    out = set(_forms(word))
    for fam in _TOPIC_FAMILIES:
        if out & fam:
            out |= fam
    return out


def _goal_items(text: str) -> list[str]:
    """Her listed jobs, in her words (the needs rules' reading), ``[]`` when
    that reading is not in this build or fails (then nothing is added)."""
    try:
        from puffpai.catalogs import needs_rules
        return [str(x) for x in needs_rules.explicit_items(text)][:12]
    except Exception:  # noqa: BLE001 - fail closed: no job subject words
        return []


def _job_subject_words(goal: str, home: dict | None, subject: str
                       ) -> tuple[set[str], str]:
    """The bot's subject words beyond its kind phrase: ``(word forms,
    topic phrase)``.

    The subject rule applies to every held class, and health subject words are
    sent with the plain note. Over-holding starves discovery: "drug" of "drug
    name flashcards", "medicine" of a pet's "vet visits, vaccines and
    medicine", "resume" and "interview" in a job hunt after a lay-off,
    "pregnancy" and "blood sugar" in a pregnancy goal would never be searched.

    * In any goal: a health word or an everyday-meaning sensitive word
      (:data:`STORY_EXEMPT`) inside one of her own listed jobs ("drug name
      flashcards", "her vet visits ... and medicine" in a pet bot) is a
      subject word. Never in a job that names one of her people ("log my
      husband's chemo") or a listed sensitive thing that is no everyday word
      ("track my hiv meds", "renew my visa"): those stay held.
    * When she names no kind of bot: every plain word of her own listed jobs
      is a subject word ("rewriting my resume", "practicing interview
      questions"), unless the goal tells a story of violence, belief, legal
      trouble, sex life or grief. When one of those jobs is about her health,
      her own health words (in sentences that name none of her people) are
      the subject too, and the first health phrase becomes the subject
      phrase so the wizard shows the plain note. Her own diet words are a
      subject word in every goal ("I went vegan").
    Personal details stay held whatever this says: names, places, numbers,
    dates, ages and her people's stories are checked before subject words.
    Used to SEND more; every word it adds is a word the screen would otherwise
    hold only by rule 5 or by the private-story rule."""
    text = _fold(goal)
    marks = _goal_marks(text, home)[0]
    subj_words = set(str(subject or "").split())
    animal = any(_forms(w) & _ANIMAL_WORDS for w in subj_words)
    raw = goal_words.raw_words(text)
    cats = {_listed_category(w, raw[k - 1] if k else "") for k, w in enumerate(raw)
            if not (_forms(w) & STORY_EXEMPT)} - {""}
    cats |= {c for _seq, c in sensitive_phrase_hits(raw)}
    story = set(_story_holds(text))

    def held_why(w: str) -> str:
        got = marks.held.get(w) or marks.held.get(goal_words.singular(w))
        return got[0] if got else ""

    def healthy(w: str) -> bool:
        return bool(is_health_word(w) or sensitive_why(w) == "health"
                    or held_why(w) == "health" or _forms(w) & DIET_WORDS)

    out: set[str] = set()
    health_job = False
    item_words: set[str] = set()
    # A health story of one of her people anywhere ("my son with diabetes")
    # ends the pet exception ("her"/"his" may then be that person) and no
    # sensitive word of a job is the bot's own: "track insulin" may be his.
    theirs_health = any(healthy(w) for w in story)
    if theirs_health:
        animal = False

    def names_someone(lows: list[str]) -> bool:
        return any(_kin(w) or w in _ITEM_PEOPLE or (w in _ITEM_THEIRS and not animal)
                   or (held_why(w) == "name" and _forms(w) & marks.person)
                   or _forms(w) & (AUDIENCE | BEINGS | _HUMAN_BEINGS) for w in lows)

    def content(w: str) -> bool:
        return not (not w or not w.isalpha() or len(w) < 2 or w in FUNCTION_WORDS
                    or w in PRONOUNS or w in POSSESSIVES or w in BOT_NOUNS
                    or w in goal_words.stopwords() or is_hard_word(w) or w in marks.hard)

    items = []
    for item in _goal_items(text):
        # The reason she gives after a job ("practicing interview questions
        # because I freeze up") is not the job.
        item = re.split(r"\b(?:because|since|cause|cuz|as)\b", item, maxsplit=1,
                        flags=re.I)[0]
        items.append([_base(t) for t in _tokens(item)])
    # "a reading bot for my son, dyslexia worksheets": a list that names one
    # of her people as an item lists that person's things, not her jobs.
    person_list = any(lows and names_someone(lows)
                      and not any(content(w) and not names_someone([w]) for w in lows)
                      for lows in items)
    for lows in items:
        # A job that names someone ("my nephew", "spectrum kid", "her
        # meds") is theirs: its sensitive words stay held.
        if names_someone(lows):
            continue
        # A listed word that is no everyday word ("chemo", "visa"), or one
        # that may be either ("AA batteries", "AA meetings"): the job stays
        # held.
        if any((_listed_category(w) or _forms(w) & AMBIGUOUS)
               and not (_forms(w) & _EVERYDAY_SENSITIVE) for w in lows):
            continue
        if any(w in story for w in lows):
            continue
        words = [w for w in lows if content(w)
                 and held_why(w) not in ("name", "brand", "place", "region", "date")]
        plain = [w for w in words if not (healthy(w) or _forms(w) & STORY_EXEMPT
                                          or sensitive_category(w))]

        def names_a_thing(w: str) -> bool:
            """With a kind of bot, a sensitive word is the job's own only
            when it names a kind of thing ("drug name flashcards"): no
            my/our/his/her in front ("track my drinking" is hers) and a
            plain word right after it. A pet's things need neither."""
            if not subject or animal:
                return True
            k = lows.index(w)
            if any(x in POSSESSIVES for x in lows[max(0, k - 2):k]):
                return False
            return k + 1 < len(lows) and lows[k + 1] in plain
        for w in words:
            item_words.update(_forms(w))
            if w not in plain:
                if not names_a_thing(w):
                    continue
                # A sensitive word is the job's own only beside a plain job
                # word ("drug name flashcards", "tracks vaccines and
                # medicine"), never alone ("dyslexia") or in a person's list.
                # In a goal that names a listed sensitive thing besides the
                # kind of bot ("a planner bot ... my chemo appointments and my
                # radiation schedule") no job word of that kind leaves.
                if (plain and not person_list and not theirs_health
                        and (not subject or not cats)):
                    health_job = health_job or healthy(w)
                    out |= _family(w)
            elif not subject and not (cats & _STORY_ONLY_CATS):
                out |= _forms(w)
    topic = ""
    if not subject and not (cats & _STORY_ONLY_CATS):
        clauses = [c for c in _clause_words(text)
                   if not any(_kin(w) or w in _ITEM_PEOPLE or w in _ITEM_THEIRS
                              or (held_why(w) == "name") for w in c)]
        own = {w for c in clauses for w in c}
        for c in clauses:
            for w in c:
                if not w.isalpha() or is_hard_word(w) or w in marks.hard:
                    continue
                if _forms(w) & DIET_WORDS and w not in story:
                    out |= _family(w)
                elif (health_job and healthy(w)
                      and held_why(w) not in ("name", "brand", "place", "date")):
                    out |= _family(w)
        if health_job:
            # The subject phrase: a health phrase of hers that one of her
            # jobs names ("blood sugar" for "keeping track of my blood sugar
            # numbers"), else the first one of hers.
            from puffpai.security.data_classifier import health_terms
            terms: list[list[str]] = []
            for term in health_terms(text):
                words = [w for w in goal_words.raw_words(term) if len(w) > 1]
                if (words and len(words) <= 3 and set(words) <= own
                        and all(w.isalpha() and _forms(w) & out for w in words)):
                    terms.append(words)
            terms.sort(key=lambda ws: 0 if any(_forms(w) & item_words for w in ws) else 1)
            if terms:
                topic = " ".join(terms[0])
    else:
        for c in _clause_words(text):
            if any(_kin(w) or w in _ITEM_PEOPLE or w in _ITEM_THEIRS for w in c):
                continue
            for w in c:
                if w.isalpha() and _forms(w) & DIET_WORDS and w not in story:
                    out |= _family(w)
    return {f for f in out if f.isalpha()}, topic


_MODEL_YEAR_RE = re.compile(r"(?:19|20)\d\d")


def _model_words(goal: str, home: dict | None, subject: str) -> set[str]:
    """The make and model of the machine the bot is about, said after its
    model year ("my 2016 Subaru Outback", "my son's 2020 Mazda3" in a car
    bot): subject words, searched like the kind of bot. The year itself stays
    held (years are hard). Only when the kind phrase names a
    machine (car, truck, printer, ...); never a word the goal holds as a
    person's name or a place. The subject rule for vehicle model and year
    (so "mazda3" is searched)."""
    if not any(_forms(w) & _MACHINE_NOUNS for w in str(subject or "").split()):
        return set()
    text = _fold(goal)
    marks = _goal_marks(text, home)[0]
    out: set[str] = set()
    for off, sent in _spans(text, _SENT_RE):
        toks, seps, _ = _tok_seps(sent, off)
        for i, tok in enumerate(toks[:-1]):
            if not _MODEL_YEAR_RE.fullmatch(tok):
                continue
            k = i + 1
            while k < len(toks) and k <= i + 3 and _is_cap(toks[k]) \
                    and not _CLAUSE_BREAK_RE.search(seps[k]):
                b = _base(toks[k])
                why = (marks.held.get(b) or ("",))[0]
                if (not b.isalnum() or b.isdigit() or _forms(b) & marks.person
                        or why in ("place", "region", "date") or is_hard_word(b) and not
                        (_has_digit(b) and any(c.isalpha() for c in b))):
                    break
                out |= _forms(b)
                k += 1
    return out


def _bot_subject(goal: str, home: dict | None) -> tuple[str, list[str], set[str]]:
    """``(subject phrase, notes, more subject word forms)``: the kind phrase
    (:func:`_subject`), the money words of a money bot
    (:func:`subject_domain_words`) and the job and topic words
    (:func:`_job_subject_words`). With no kind phrase, a health topic of her
    own is the subject phrase, with the health note."""
    folded = _fold(goal)
    marks, _, places = _goal_marks(folded, home)
    subject, notes, refused = _bot_noun_subject(folded, marks, places)
    if not subject and not refused:
        subject, notes = _product_subject(folded, marks)
    also = subject_domain_words(goal, subject)
    if refused:
        # A sensitive kind of bot with one of her people in it ("my wife is
        # pregnant, a pregnancy tracker bot"): nothing of it is searched,
        # and no job or topic word is made a subject word either.
        return "", [], also
    job, topic = _job_subject_words(goal, home, subject)
    job |= _model_words(goal, home, subject)
    also |= job
    if not subject and topic:
        subject, notes = topic, ["health"]
    _LAST_JOB_WORDS[(str(goal or ""), json.dumps(home, sort_keys=True, default=str)
                     if home else "")] = set(job)
    while len(_LAST_JOB_WORDS) > 64:
        _LAST_JOB_WORDS.pop(next(iter(_LAST_JOB_WORDS)))
    return subject, list(notes), also


#: The job, topic and model words of the last goals (see :func:`find`).
_LAST_JOB_WORDS: dict[tuple[str, str], set[str]] = {}


def private_goal_words(goal: str, *, home: dict | None = None) -> dict[str, str]:
    """Every word of a goal that tells a private story, and why.

    No word list can hold every word of such a story ("renew my visa",
    "visit her grave", "log the bruises", "tinder dates"). When the goal says
    something from a sensitive category (mental health, addiction, sexual
    health, sex life, identity, belief, legal trouble, money hardship,
    violence, grief) that is NOT the bot's own subject, the whole goal is
    private: every one of its words except the subject's (and joining
    words) is held and can never be ticked. Only the kind of bot is
    searched, with its plain note when it is sensitive itself (owner's
    answer 3). A goal whose sensitive words all name the bot ("a sobriety
    tracker bot") is not made private by them.

    Returns ``{word form: "health" | "private"}``; ``{}`` for an ordinary
    goal.
    """
    text = _fold(goal)
    subject, _notes, also = _bot_subject(goal, home)
    subject_forms: set[str] = set()
    for w in subject.split():
        subject_forms |= _forms(w)
    subject_forms |= also
    raw = goal_words.raw_words(text)
    in_dates = _date_positions(raw)
    plain_conditions = _plain_job_conditions(text)
    cats: list[str] = []
    for k, w in enumerate(raw):
        if _forms(w) & STORY_EXEMPT or k in in_dates or _forms(w) & plain_conditions:
            continue
        cat = _listed_category(w, raw[k - 1] if k else "")
        if cat and not (_forms(w) & subject_forms):
            cats.append(cat)
    for seq, cat in sensitive_phrase_hits(raw):
        if not all(_forms(x) & subject_forms for x in seq):
            cats.append(cat)
    if not cats:
        return {}
    why = "health" if all(c == "health" for c in cats) else "private"
    # In a private story ("out" of "coming out", "at" of "yells at me") the
    # joining words are held too. Only the subject and the bot nouns may leave.
    keep = BOT_NOUNS
    # The job she asks for after the condition ("count calories", "avoid
    # bars", "stop purging") is held too unless it is the bot's subject:
    # what one of her people with a condition wants done tells the story.
    out: dict[str, str] = {}
    for w in raw:
        if not w or w in keep or _forms(w) & subject_forms:
            continue
        for f in word_keys(w) | _forms(w):
            if f not in subject_forms:
                out[f] = why
    return out


def find(goal: str, *, home: dict | None = None) -> PersonalWords:
    """The goal's hard words, held words and subject. Nothing is logged."""
    marks, _, _ = _goal_marks(goal, home)
    subject, notes, also = _bot_subject(goal, home)
    subject_words = set()
    for w in subject.split():
        subject_words |= _forms(w)
    subject_words |= also
    held: list[dict] = []
    seen: set[str] = set()
    for word in marks.typed:
        status = marks.held.get(word) or marks.held.get(goal_words.singular(word))
        if status is None or word in seen:
            continue  # it became hard, or it is already listed
        why, can_allow = status
        # A proper noun or brand the subject is made of is searched, not held;
        # so is a season or holiday the bot is about ("a summer camp bot").
        if _forms(word) & subject_words and (
                (why in ("name", "brand") and can_allow) or why == "date"):
            continue
        # A subject word held only because a sensitive pair holds it ("log my
        # AA meetings" in "a meeting tracker bot"): the subject is searched,
        # so listing it as kept private would be untrue. The pair's own
        # sensitive word stays held.
        if (_forms(word) & subject_words and why in ("health", "private")
                and not sensitive_category(word) and not is_health_word(word)):
            continue
        parts = word.split()
        if len(parts) > 1:
            # A pair ("green card"): not listed when it is the subject; when
            # the subject holds some of it, only the rest is the detail.
            rest = [p for p in parts if not (_forms(p) & subject_words)
                    or sensitive_category(p) or is_health_word(p)]
            if not rest:
                continue
            if len(rest) < len(parts):
                word = " ".join(rest)
                if word in seen:
                    continue
        seen |= _forms(word)
        held.append({"word": word, "why": why, "can_allow": bool(can_allow)})
    # A neighbour of a sensitive word ("custody hearing") is held only where
    # the goal does not already keep every word but the subject's (else a
    # private story's ordinary words, "check", "ins", "appointments", are
    # listed as kept private one by one).
    # A word a listed pair already names is not listed again ("panic" next
    # to "panic attack"): one line per detail.
    in_pairs: set[str] = set()
    for row in held:
        parts = str(row["word"]).split()
        if len(parts) > 1:
            for p in parts:
                in_pairs |= _forms(p)
    held = [row for row in held if len(str(row["word"]).split()) > 1
            or not (_forms(str(row["word"])) & in_pairs)]
    story = private_goal_words(goal, home=home)
    for word, why in ([] if story else _sensitive_neighbours(_fold(goal), subject_words)):
        if word in seen or goal_words.singular(word) in seen or word in marks.hard:
            continue
        seen |= _forms(word)
        held.append({"word": word, "why": why, "can_allow": False})
    pieces = {k: set(v) for k, v in marks.pieces.items()}
    # A word typed with letters of another alphabet ("Maгia", "Зoe") holds
    # every reading of it, so a model's reading ("magia") is held too. Only
    # ever holds more.
    spell = other_spellings(goal)
    if spell:
        by_sound: Optional[_Marks] = None
        for look, others in sorted(spell.items()):
            row = next((r for r in held if r["word"] in _forms(look)), None)
            if row is not None:
                pieces.setdefault(str(row["word"]), set()).update(others)
                continue
            if by_sound is None:
                by_sound = _goal_marks(_fold(goal, other_letters="sound"), home)[0]
            for other in sorted(others):
                status = by_sound.held.get(other)
                if not status or status[0] not in ("name", "brand", "place") \
                        or other in seen or _forms(other) & subject_words:
                    continue
                seen |= _forms(other)
                held.append({"word": other, "why": status[0], "can_allow": status[1]})
                pieces.setdefault(other, set()).update((others | {look}) - {other})
    return PersonalWords(hard=set(marks.hard), held=held, subject=subject,
                         subject_notes=notes, dates=set(marks.dates),
                         date_seqs=list(marks.date_seqs),
                         pieces=pieces, plan_time=marks.plan_time(), subject_also=also,
                         quiet=set(marks.quiet) - subject_words,
                         subject_job=set(_LAST_JOB_WORDS.get(
                             (str(goal or ""), json.dumps(home, sort_keys=True, default=str)
                              if home else ""), set())))


__all__ = ["PersonalWords", "find", "subject_of", "dict_kind", "is_common_word",
           "is_hard_word", "without_hard_tokens", "is_name_like",
           "is_health_word", "home_words", "goal_holds_place", "RELATIVES",
           "AUDIENCE", "PRONOUNS", "LIFE_EVENTS", "MONTHS", "WEEKDAYS",
           "NUMBER_WORDS", "REGION_WORDS", "REGION_PHRASES", "LANGUAGES",
           "DIET_WORDS", "BOT_NOUNS", "SENSITIVE_WORDS", "SENSITIVE_PHRASES",
           "SUBJECT_NOTES", "sensitive_category", "sensitive_why",
           "sensitive_phrase_hits", "loose_forms", "word_keys", "private_goal_words",
           "STORY_EXEMPT", "subject_note_kind", "PRIVATE_SUBJECT_NOTE", "stem_bases",
           "listed_lookup", "AMBIGUOUS", "OWNED_HABITS", "PLAIN_SEASONS", "PLAN_NOUNS",
           "HOLIDAYS", "DATE_PHRASES", "date_phrase_hits", "name_pieces", "glued_to"]
