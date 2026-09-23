package dataset

import (
	"encoding/json"
	"fmt"
	"math"
	"reflect"
	"strings"
	"testing"
)

func researchFloat(v float64) *float64 { return &v }
func researchText(v string) *string    { return &v }

func TestSignalFieldsFilterWithoutTreatingFrequencySpansAsChannels(t *testing.T) {
	f := researchTestFixture(t, false)
	f.manifest.Research.Version = "entity-research-v2"
	f.manifest.Research.Fields = researchCatalogForVersion("entity-research-v2")
	member := "entities/" + strings.Replace(firstID, ":", "/", 1) + ".json"
	var record map[string]json.RawMessage
	var facts []ResearchRawFact
	var claims []ResearchClaim
	if err := json.Unmarshal(f.members[member], &record); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(record["facts"], &facts); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(f.members["research/claims.json"], &claims); err != nil {
		t.Fatal(err)
	}
	add := func(field, name, raw string, value *ResearchValue) {
		fact := ResearchRawFact{Name: name, Raw: raw, Evidence: "https://sample.test/0/0", Values: []float64{}}
		claims = append(claims, ResearchClaim{ID: fmt.Sprintf("claim:%024x", len(claims)+1), Entity: 0, Field: field,
			EvidenceID: digestString([]byte(fact.Evidence))[:24], Locator: ResearchLocator{"fact", len(facts)}, Raw: fact, Status: "known", Value: value})
		facts = append(facts, fact)
	}
	add("modulation", "Modulation", "GMSK", &ResearchValue{Text: researchText("GMSK")})
	add("bandwidth", "Bandwidth", "25 kHz", &ResearchValue{Number: &ResearchNumber{researchFloat(25000), researchFloat(25000), true, true, "Hz"}})
	add("frequency_range", "Reported frequency range", "161.975 MHz — 162.025 MHz", &ResearchValue{Number: &ResearchNumber{researchFloat(161975000), researchFloat(162025000), true, true, "Hz"}})
	record["facts"] = rawJSON(t, facts)
	f.members[member] = rawJSON(t, record)
	f.members["research/claims.json"] = rawJSON(t, claims)
	f.manifest.Research.Claims = len(claims)
	d := f.open(t)
	if err := d.Verify(); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		where []string
		total int
	}{
		{[]string{"modulation = GMSK", "bandwidth = 25 kHz"}, 1},
		{[]string{"frequency_range >= 161 MHz", "frequency_range <= 163 MHz"}, 1},
		{[]string{"frequency_range = 162 MHz"}, 0},
		{[]string{"frequency = 162 MHz"}, 0},
		{[]string{"modulation = FMCW"}, 0},
	} {
		rows, total, err := d.List(Filter{Where: tc.where}, 10)
		if err != nil || total != tc.total || total == 1 && rows[0].ID != firstID {
			t.Fatalf("%v: %v %d %v", tc.where, rows, total, err)
		}
	}
	legacy := researchTestFixture(t, false).open(t)
	if err := legacy.Verify(); err != nil {
		t.Fatal(err)
	}
	if _, _, err := legacy.List(Filter{Where: []string{"modulation = GMSK"}}, 10); err == nil {
		t.Fatal("legacy catalog accepted an undeclared field")
	}
}

func researchTestFixture(t *testing.T, observations bool) *imageBundleFixture {
	t.Helper()
	f := newImageFixture(t)
	if observations {
		f = newObservationFixture(t)
	}
	f.manifest.Research = &ResearchManifest{Version: "entity-research-v1", Fields: researchCatalog()}
	claims := []ResearchClaim{}
	for i, id := range []string{firstID, secondID} {
		member := "entities/" + strings.Replace(id, ":", "/", 1) + ".json"
		var record map[string]any
		if err := json.Unmarshal(f.members[member], &record); err != nil {
			t.Fatal(err)
		}
		page := record["evidence"].([]any)[0].(map[string]any)
		page["retrieved_at"] = "2025-03-04T05:06:07+00:00"
		facts := []ResearchRawFact{}
		add := func(field, name, raw, status string, value *ResearchValue) {
			fact := ResearchRawFact{Name: name, Raw: raw, Evidence: page["url"].(string), Values: []float64{}}
			claim := ResearchClaim{ID: fmt.Sprintf("claim:%024x", len(claims)+1), Entity: i, Field: field, EvidenceID: page["id"].(string), Locator: ResearchLocator{"fact", len(facts)}, Raw: fact, Status: status, Value: value}
			facts = append(facts, fact)
			claims = append(claims, claim)
		}
		if i == 0 {
			add("manufacturer", "Manufacturer", "Acme & Co.", "known", &ResearchValue{Text: researchText("Acme & Co.")})
			add("range", "Range", "100-200 m", "known", &ResearchValue{Number: &ResearchNumber{researchFloat(100), researchFloat(200), true, true, "m"}})
			add("range", "Range", "500 m", "known", &ResearchValue{Number: &ResearchNumber{researchFloat(500), researchFloat(500), true, true, "m"}})
			add("mass", "Mass", "approximately 1 t", "approximate", &ResearchValue{Number: &ResearchNumber{researchFloat(1000), researchFloat(1000), true, true, "kg"}})
			add("origin_country", "Country of origin", "unknown", "unknown", nil)
			add("frequency", "Frequency", "L-band", "unparsed", nil)
			add("service_entry", "Service entry", "2000", "known", &ResearchValue{Date: &ResearchDate{"2000-01-01", "2000-12-31", "year"}})
		} else {
			add("manufacturer", "Manufacturer", "Beta", "known", &ResearchValue{Text: researchText("Beta")})
			add("range", "Range", "300 m", "known", &ResearchValue{Number: &ResearchNumber{researchFloat(300), researchFloat(300), true, true, "m"}})
			add("service_entry", "Service entry", "2000-03-01", "known", &ResearchValue{Date: &ResearchDate{"2000-03-01", "2000-03-01", "day"}})
		}
		record["facts"] = facts
		f.members[member] = rawJSON(t, record)
		claims = append(claims, ResearchClaim{ID: fmt.Sprintf("claim:%024x", len(claims)+1), Entity: i, Field: "captured_date", EvidenceID: page["id"].(string), Locator: ResearchLocator{"captured_at", 0},
			Raw: ResearchRawFact{Name: "Captured at", Raw: page["retrieved_at"].(string), Evidence: page["url"].(string), Values: []float64{}}, Status: "known", Value: &ResearchValue{Date: &ResearchDate{"2025-03-04", "2025-03-04", "day"}}})
	}
	f.members["research/claims.json"] = rawJSON(t, claims)
	f.manifest.Research.Claims = len(claims)
	relations := []ResearchRelation{{ID: "relation:" + strings.Repeat("a", 24), Source: 0, Target: 1, Type: "related_system", Basis: "reviewed_source_evidence", Rationale: "Synthetic source statements describe related systems.", Evidence: []ResearchReference{
		{Entity: 0, EvidenceID: digestString([]byte("https://sample.test/0/0"))[:24], Quote: "Full content"},
		{Entity: 1, EvidenceID: digestString([]byte("https://sample.test/1/0"))[:24], Quote: "Full content"},
	}}}
	f.members["research/relations.json"] = rawJSON(t, relations)
	f.manifest.Research.Relations = 1
	return f
}

func mutateResearchRows(t *testing.T, f *imageBundleFixture, member string, edit func([]map[string]any)) {
	t.Helper()
	var rows []map[string]any
	if err := json.Unmarshal(f.members[member], &rows); err != nil {
		t.Fatal(err)
	}
	edit(rows)
	f.members[member] = rawJSON(t, rows)
}

func TestResearchInspectionPreservesClaimsAndSourceIdentity(t *testing.T) {
	d := researchTestFixture(t, false).open(t)
	if err := d.Verify(); err != nil {
		t.Fatal(err)
	}
	claims, err := d.ResearchFacts(firstID)
	if err != nil || len(claims) != 8 {
		t.Fatal(claims, err)
	}
	for _, claim := range claims {
		if claim.EntityID != firstID || claim.EvidenceURL != "https://sample.test/0/0" || claim.Raw.Evidence != claim.EvidenceURL {
			t.Fatal(claim)
		}
	}
	if claims[3].Status != "approximate" || claims[4].Status != "unknown" || claims[4].Value != nil || claims[5].Status != "unparsed" {
		t.Fatal("source uncertainty was discarded", claims)
	}
	comparison, err := d.Compare([]string{secondID, firstID})
	if err != nil || len(comparison) != len(researchCatalog()) {
		t.Fatal(comparison, err)
	}
	for _, row := range comparison {
		if row.Entities[0].EntityID != secondID || row.Entities[1].EntityID != firstID {
			t.Fatal("comparison reordered requested IDs", row)
		}
		if row.Field == "range" && (len(row.Entities[0].Claims) != 1 || len(row.Entities[1].Claims) != 2) {
			t.Fatal("comparison selected one truth", row)
		}
		if row.Field == "origin_country" && (row.Entities[0].Status != "unknown" || row.Entities[0].Claims == nil || row.Entities[1].Status != "claims" || row.Entities[1].Claims[0].Status != "unknown") {
			t.Fatal("absent and source-unknown values conflated", row)
		}
	}
	for _, id := range []string{firstID, secondID} {
		relations, err := d.Relationships(id, "related_system")
		if err != nil || len(relations) != 1 || relations[0].SourceID != firstID || relations[0].TargetID != secondID || relations[0].Evidence[1].EvidenceURL != "https://sample.test/1/0" {
			t.Fatal(relations, err)
		}
	}
	if relations, err := d.Relationships(firstID, "equivalent"); err != nil || len(relations) != 0 {
		t.Fatal("related relation became equivalence", relations, err)
	}
	if _, err := d.Compare([]string{firstID, firstID}); err == nil {
		t.Fatal("duplicate compare ID accepted")
	}
	if _, err := d.Relationships(firstID, "similar"); err == nil {
		t.Fatal("unsupported relation type accepted")
	}
}

func TestResearchFiltersKeepUnitsRangesUncertaintyAndClaimConjunctions(t *testing.T) {
	d := researchTestFixture(t, false).open(t)
	for _, test := range []struct {
		where []string
		ids   []string
	}{
		{[]string{"manufacturer = ＡＣＭＥ   & co."}, []string{firstID}},
		{[]string{"manufacturer = Acme Co"}, nil},
		{[]string{"manufacturer != Beta"}, []string{firstID}},
		{[]string{"origin_country != Russia"}, nil},
		{[]string{"mass = 1000 kg"}, nil},
		{[]string{"mass != 999 kg"}, nil},
		{[]string{"frequency != 1 MHz"}, nil},
		{[]string{"range >= 2.5e-1 km"}, []string{firstID, secondID}},
		{[]string{"range < 201 m"}, []string{firstID}},
		{[]string{"range = 200 m"}, nil},
		{[]string{"range = 0.3 km"}, []string{secondID}},
		{[]string{"range != 300 m"}, []string{firstID}},
		{[]string{"range > 400 m", "range < 200 m"}, nil},
		{[]string{"range >= 100 m", "range <= 200 m"}, []string{firstID}},
		{[]string{"service_entry = 2000"}, []string{firstID}},
		{[]string{"service_entry >= 2000", "service_entry <= 2000"}, []string{firstID, secondID}},
		{[]string{"service_entry = 2000-03-01"}, []string{secondID}},
		{[]string{"service_entry != 2000"}, nil},
		{[]string{"service_entry < 2000-06"}, []string{secondID}},
		{[]string{"service_entry >= 2000-03-01"}, []string{secondID}},
		{[]string{"service_entry < 2001"}, []string{firstID, secondID}},
		{[]string{"captured_date = 2025-03-04", "service_entry < 2001", "manufacturer = Beta"}, []string{secondID}},
	} {
		t.Run(strings.Join(test.where, ";"), func(t *testing.T) {
			entities, total, err := d.List(Filter{Where: test.where}, 100)
			if err != nil {
				t.Fatal(err)
			}
			var ids []string
			for _, entity := range entities {
				ids = append(ids, entity.ID)
			}
			if total != len(test.ids) || !reflect.DeepEqual(ids, test.ids) {
				t.Fatal(ids, total, test.ids)
			}
		})
	}
	entities, total, err := d.List(Filter{Where: []string{"range > 50 m"}}, 1)
	if err != nil || len(entities) != 1 || total != 2 || entities[0].ID != firstID || d.vectors != nil || d.images != nil || d.observations != nil {
		t.Fatal("list loaded models/vectors or lost total/order", entities, total, err)
	}
}

func TestResearchFilteringPrecedesRankingInEveryModality(t *testing.T) {
	f := researchTestFixture(t, true)
	withRanking(f)
	d := f.open(t)
	text, image := make([]float32, 384), make([]float32, 512)
	text[0], image[0] = 1, 1
	filter := Filter{Where: []string{"manufacturer = Beta"}}
	for _, hybrid := range []bool{false, true} {
		results, err := d.Search(text, "Weather sensor", hybrid, filter, 1, "")
		if err != nil || len(results) != 1 || results[0].ID != secondID {
			t.Fatal("text filter leaked", results, err)
		}
		if hybrid && (results[0].Ranking.SemanticRank != 1 || results[0].Ranking.LexicalRank != 1) {
			t.Fatal("text ranks calculated before eligibility", results)
		}
		results, err = d.Search(text, "", false, filter, 1, secondID)
		if err != nil || len(results) != 0 {
			t.Fatal("similar excluded entity returned", results, err)
		}
	}
	for _, combined := range []bool{false, true} {
		var query []float32
		if combined {
			query = text
		}
		results, err := d.SearchImages(image, query, "Cheese Board", filter, 1)
		if err != nil || len(results) != 1 || results[0].ID != secondID {
			t.Fatal("image/combined filter leaked", results, err)
		}
		if combined && math.Abs(results[0].Score-2.0/61) > 1e-12 {
			t.Fatal("fusion ranks calculated before eligibility", results)
		}
	}
	text[0], text[2] = 0, 1
	for _, hybrid := range []bool{false, true} {
		results, err := d.SearchObservations(text, "Маркировка", hybrid, Filter{Where: []string{"manufacturer=Acme & Co."}}, 1)
		if err != nil || len(results) != 1 || results[0].ID != firstID {
			t.Fatal("generated match bypassed filter", results, err)
		}
		for _, match := range results[0].Matches {
			if match.Origin == "generated" {
				t.Fatal("ineligible observation leaked", match)
			}
		}
	}
	results, err := d.SearchImagesWithObservations(image, text, "Маркировка", filter, 1)
	if err != nil || len(results) != 1 || results[0].ID != secondID || math.Abs(results[0].Score-2.0/61) > 1e-12 {
		t.Fatal("generated combined filter/rank leaked", results, err)
	}
	empty := Filter{Where: []string{"manufacturer=absent"}}
	if rows, err := d.SearchImagesWithObservations(image, text, "Маркировка", empty, 1); err != nil || len(rows) != 0 {
		t.Fatal("combined union restored excluded entity", rows, err)
	}
}

func TestResearchFiltersRecompileAfterCallerChangesOrDatasetChanges(t *testing.T) {
	d := researchTestFixture(t, false).open(t)
	filter, err := d.PrepareFilter(Filter{Where: []string{"manufacturer=Beta"}})
	if err != nil {
		t.Fatal(err)
	}
	again, err := d.PrepareFilter(filter)
	if err != nil || again.prepared != filter.prepared {
		t.Fatal("prepared predicate was repeated", err)
	}
	filter.Where[0] = "manufacturer=Acme & Co."
	rows, _, err := d.List(filter, 1)
	if err != nil || len(rows) != 1 || rows[0].ID != firstID {
		t.Fatal("stale filter reused", rows, err)
	}
	filter.Where = nil
	_, total, err := d.List(filter, 1)
	if err != nil || total != 2 {
		t.Fatal("cleared predicates kept eligibility", total, err)
	}
	filter = again
	f := researchTestFixture(t, false)
	mutateResearchRows(t, f, "research/claims.json", func(rows []map[string]any) {
		for _, row := range rows {
			if row["field"] == "manufacturer" {
				row["status"], row["value"] = "unparsed", nil
			}
		}
	})
	_, total, err = f.open(t).List(filter, 100)
	if err != nil || total != 0 {
		t.Fatal("other dataset reused eligibility", total, err)
	}
}

func TestResearchMalformedPredicatesFailBeforeExpensiveSearch(t *testing.T) {
	d := researchTestFixture(t, true).open(t)
	for _, expression := range []string{"range", "range=", "bogus=1", "range=NaN m", "range=Inf m", "range=1e999 km", "range=1e308 km", "range=3", "range=3 kg", "mass=1 tons", "speed=1 Mach", "range=1-2 m", "manufacturer<Acme", "service_entry=2000-02-30", "service_entry=0000", "service_entry=2000-2", "manufacturer=Acme\x00Co", "range == 2m", "range=1 Mm", "power=1 mw", "frequency=1 MHZ"} {
		if _, err := d.PrepareFilter(Filter{Where: []string{expression}}); err == nil {
			t.Fatal("accepted bad predicate", expression)
		}
	}
	if d.research != nil || d.vectors != nil || d.images != nil {
		t.Fatal("malformed predicate loaded data")
	}
	if _, err := d.PrepareFilter(Filter{Where: make([]string, 33)}); err == nil {
		t.Fatal("unbounded predicate list accepted")
	}
}

func TestResearchOpenBoundsConversionAndDateBoundaries(t *testing.T) {
	field := ResearchField{"range", "number", "m"}
	catalog := map[string]ResearchField{"range": field}
	claim := ResearchClaim{Status: "known", Value: &ResearchValue{Number: &ResearchNumber{nil, researchFloat(100), false, false, "m"}}}
	for _, expression := range []string{"range<100 m", "range<=100 m", "range!=100 m"} {
		predicate, err := parseResearchPredicate(expression, catalog)
		if err != nil || !predicate.matches(claim) {
			t.Fatal(expression, err)
		}
	}
	claim.Value.Number = &ResearchNumber{researchFloat(100), nil, false, false, "m"}
	for _, expression := range []string{"range>100 m", "range>=100 m", "range!=100 m"} {
		predicate, err := parseResearchPredicate(expression, catalog)
		if err != nil || !predicate.matches(claim) {
			t.Fatal(expression, err)
		}
	}
	for _, test := range []struct {
		field, unit, query string
		canonical          float64
	}{
		{"range", "m", "1 ft", .3048}, {"range", "m", "1 in", .0254}, {"range", "m", "1 mi", 1609.344}, {"range", "m", "1 nmi", 1852},
		{"mass", "kg", "1 lb", .45359237}, {"mass", "kg", "1 metric ton", 1000}, {"speed", "m/s", "1 km/h", 1.0 / 3.6},
		{"speed", "m/s", "1 MPH", .44704}, {"speed", "m/s", "1 knots", 1852.0 / 3600}, {"frequency", "Hz", "1 GHz", 1e9},
		{"power", "W", "1 MW", 1e6}, {"crew", "count", "3", 3},
		{"power", "W", "1 mW", .001}, {"frequency", "Hz", "1 mHz", .001}, {"frequency", "Hz", "1 MHz", 1e6},
	} {
		field := ResearchField{test.field, "number", test.unit}
		p, err := parseResearchPredicate(test.field+"="+test.query, map[string]ResearchField{test.field: field})
		claim := ResearchClaim{Status: "known", Value: &ResearchValue{Number: &ResearchNumber{researchFloat(test.canonical), researchFloat(test.canonical), true, true, test.unit}}}
		if err != nil || !p.matches(claim) {
			t.Fatal(test, err)
		}
	}
	for _, unit := range []string{"Mm", "mw", "MHZ", "KG"} {
		if _, ok := parseResearchUnit(unit); ok {
			t.Fatal("SI symbol case changed dimension/scale", unit)
		}
	}
	if _, ok := parseResearchUnit("Hz "); !ok {
		t.Fatal("unit whitespace should normalize")
	}
	for value, expected := range map[string]string{"2000-02": "2000-02-29", "1900-02": "1900-02-28", "9999": "9999-12-31"} {
		date, err := parseResearchDate(value)
		if err != nil || date.Max != expected {
			t.Fatal(date, err)
		}
	}
}

func TestResearchSpelledUnitAliasesMatchProducerAndKeepSISeparate(t *testing.T) {
	for _, test := range []struct {
		aliases string
		unit    string
		factor  float64
	}{
		{"meter meters metre metres", "m", 1},
		{"kilometer kilometers kilometre kilometres", "m", 1000},
		{"centimeter centimeters centimetre centimetres", "m", .01},
		{"millimeter millimeters millimetre millimetres", "m", .001},
		{"ft foot feet", "m", .3048}, {"in inch inches", "m", .0254}, {"mi mile miles", "m", 1609.344}, {"nmi", "m", 1852},
		{"kilogram kilograms", "kg", 1}, {"gram grams", "kg", .001}, {"tonne tonnes", "kg", 1000},
		{"lb lbs pound pounds", "kg", .45359237}, {"kph", "m/s", 1.0 / 3.6}, {"mph", "m/s", .44704}, {"kn knot knots", "m/s", 1852.0 / 3600}, {"count", "count", 1},
	} {
		for _, alias := range strings.Fields(test.aliases) {
			for _, value := range []string{alias, strings.ToUpper(alias)} {
				unit, ok := parseResearchUnit(value)
				if !ok || unit.canonical != test.unit || unit.factor != test.factor {
					t.Fatal("producer word alias differs", value, unit)
				}
			}
		}
	}
	for _, value := range []string{"metric ton", "METRIC TONS", " metric   tons "} {
		unit, ok := parseResearchUnit(value)
		if !ok || unit.canonical != "kg" || unit.factor != 1000 {
			t.Fatal(value, unit)
		}
	}
	if normalizeResearchText("İ") != "i\u0307" || normalizeResearchText("ΟΣ") != "ος" {
		t.Fatal("text normalization must use full Unicode lowercase")
	}
}

func TestResearchUnfilteredBehaviorStaysLazyAndUnchanged(t *testing.T) {
	for _, version := range []int{2, 3, 4} {
		t.Run(fmt.Sprint(version), func(t *testing.T) {
			f := researchTestFixture(t, version == 4)
			if version == 2 {
				f.manifest.FormatVersion, f.manifest.Image = 2, nil
				for name := range f.members {
					if strings.HasPrefix(name, "image/") {
						delete(f.members, name)
					}
				}
			}
			withResearch := f.open(t)
			f.manifest.Research = nil
			delete(f.members, "research/claims.json")
			delete(f.members, "research/relations.json")
			legacy := f.open(t)
			vector := make([]float32, 384)
			vector[0] = 1
			for _, hybrid := range []bool{false, true} {
				old, err := legacy.Search(vector, "Cheese Board", hybrid, Filter{}, 2, "")
				if err != nil {
					t.Fatal(err)
				}
				current, err := withResearch.Search(vector, "Cheese Board", hybrid, Filter{}, 2, "")
				if err != nil || !reflect.DeepEqual(old, current) || withResearch.research != nil {
					t.Fatal("unfiltered results changed or loaded research", old, current, err)
				}
			}
			_, total, err := legacy.List(Filter{}, 1)
			if err != nil || total != 2 || legacy.research != nil {
				t.Fatal(total, err)
			}
			if _, err := legacy.PrepareFilter(Filter{Where: []string{"manufacturer=Beta"}}); err == nil {
				t.Fatal("legacy dataset silently ignored precise filter")
			}
		})
	}
}

func TestResearchVerifyRejectsUnusedProvenance(t *testing.T) {
	for _, mutation := range []string{"raw", "locator", "foreign_evidence", "quote", "capture"} {
		t.Run(mutation, func(t *testing.T) {
			f := researchTestFixture(t, false)
			if mutation == "quote" {
				mutateResearchRows(t, f, "research/relations.json", func(rows []map[string]any) { rows[0]["evidence"].([]any)[1].(map[string]any)["quote"] = "not captured" })
			} else {
				mutateResearchRows(t, f, "research/claims.json", func(rows []map[string]any) {
					for _, row := range rows {
						if row["entity"].(float64) != 1 {
							continue
						}
						switch mutation {
						case "raw":
							row["raw"].(map[string]any)["raw"] = "made up"
						case "locator":
							row["locator"].(map[string]any)["index"] = 99
						case "foreign_evidence":
							row["evidence_id"] = digestString([]byte("https://sample.test/0/0"))[:24]
						case "capture":
							if row["locator"].(map[string]any)["kind"] != "captured_at" {
								continue
							}
							row["locator"].(map[string]any)["index"] = 1
						}
						break
					}
				})
			}
			d := f.open(t)
			if _, err := d.ResearchFacts(firstID); err != nil {
				t.Fatal("unrelated provenance was read eagerly", err)
			}
			if err := d.Verify(); err == nil {
				t.Fatal("unused corrupt provenance accepted")
			}
		})
	}
}

func TestResearchFiltersResolveOnlyWinningClaimsOfEligibleEntities(t *testing.T) {
	f := researchTestFixture(t, false)
	mutateResearchRows(t, f, "research/claims.json", func(rows []map[string]any) {
		for _, row := range rows {
			if row["entity"].(float64) == 1 && row["field"] == "manufacturer" {
				row["raw"].(map[string]any)["raw"] = "not captured"
			}
		}
	})
	d := f.open(t)
	rows, total, err := d.List(Filter{Where: []string{"manufacturer=Acme & Co."}}, 100)
	if err != nil || total != 1 || len(rows) != 1 || rows[0].ID != firstID || d.research.records[1] != nil {
		t.Fatal("resolved unrelated source records", rows, total, err)
	}
	if err := d.Verify(); err == nil {
		t.Fatal("verify ignored nonmatching corrupt claim")
	}
	d = researchTestFixture(t, false).open(t)
	_, total, err = d.List(Filter{Where: []string{"manufacturer=Acme & Co.", "range>600 m"}}, 100)
	if err != nil || total != 0 || len(d.research.records) != 0 {
		t.Fatal("partially matching entity caused source reads", total, err)
	}
	if _, _, err := f.open(t).List(Filter{Where: []string{"manufacturer=Beta"}}, 100); err == nil {
		t.Fatal("winning corrupt claim was accepted")
	}
}

func TestResearchSchemaRejectsInvalidUnreturnedClaimsAndRelations(t *testing.T) {
	for _, mutation := range []string{"null_entity", "unknown_field", "negative_number", "wrong_unit", "null_bound_flag", "empty_interval", "both_unbounded", "wrong_kind", "unknown_value", "invalid_precision", "fake_date_range", "extra_field", "source_null", "self_relation", "non_endpoint", "same_endpoint", "null_quote"} {
		t.Run(mutation, func(t *testing.T) {
			f := researchTestFixture(t, false)
			mutateResearchRows(t, f, "research/claims.json", func(rows []map[string]any) {
				row := rows[1]
				number := row["value"].(map[string]any)["number"].(map[string]any)
				switch mutation {
				case "null_entity":
					row["entity"] = nil
				case "unknown_field":
					row["field"] = "arbitrary"
				case "negative_number":
					number["min"] = -1
				case "wrong_unit":
					number["unit"] = "kg"
				case "null_bound_flag":
					number["min_inclusive"] = nil
				case "empty_interval":
					number["max"] = 99
				case "both_unbounded":
					number["min"], number["max"], number["min_inclusive"], number["max_inclusive"] = nil, nil, false, false
				case "wrong_kind":
					row["value"] = map[string]any{"text": "100"}
				case "unknown_value":
					row["status"] = "unknown"
				case "invalid_precision":
					rows[6]["value"].(map[string]any)["date"].(map[string]any)["precision"] = "decade"
				case "fake_date_range":
					rows[6]["value"].(map[string]any)["date"].(map[string]any)["max"] = "2001-12-31"
				case "extra_field":
					row["confidence"] = 1
				}
			})
			mutateResearchRows(t, f, "research/relations.json", func(rows []map[string]any) {
				row := rows[0]
				refs := row["evidence"].([]any)
				switch mutation {
				case "source_null":
					row["source"] = nil
				case "self_relation":
					row["target"] = 0
				case "non_endpoint":
					refs[0].(map[string]any)["entity"] = 99
				case "same_endpoint":
					refs[1].(map[string]any)["entity"] = 0
				case "null_quote":
					refs[1].(map[string]any)["quote"] = nil
				}
			})
			if err := f.open(t).Verify(); err == nil {
				t.Fatal("accepted malformed research")
			}
		})
	}
}
