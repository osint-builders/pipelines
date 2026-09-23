package dataset

import (
	"math"
	"testing"
)

func TestSpecificationsKeepPropertiesUnitsIntervalsAndUncertainty(t *testing.T) {
	claim := ResearchClaim{Field: "power", Raw: ResearchRawFact{Name: "Specifications: Peak power"}, Status: "known",
		Value: &ResearchValue{Number: &ResearchNumber{researchFloat(15000), researchFloat(15000), true, true, "W"}}}
	for _, test := range []struct {
		query string
		match bool
	}{
		{"Peak power: 15 kW", true},
		{"power: 15,000 W", true},
		{"average power: 15 kW", false},
		{"power: 15 km", false},
		{"power: 15 mW", false},
		{"power: 10 - 20 kW", false},
		{"power: 15 kW; power: 15000 W", true},
	} {
		terms := querySpecifications(test.query)
		if len(terms) != 1 || terms[0].matches(claim) != test.match {
			t.Fatalf("%q: %+v", test.query, terms)
		}
	}
	term := querySpecifications("power: 15 kW")[0]
	claim.Status = "approximate"
	if term.matches(claim) {
		t.Fatal("approximate claim earned exact specification credit")
	}
	claim.Status = "known"
	claim.Value.Number.MaxInclusive = false
	if term.matches(claim) {
		t.Fatal("excluded bound earned specification credit")
	}
	claim.Value.Number = &ResearchNumber{researchFloat(15000), researchFloat(20000), true, true, "W"}
	if term.matches(claim) || !querySpecifications("power: 15 to 20 kW")[0].matches(claim) {
		t.Fatal("scalar and interval conflated")
	}
	for _, query := range []string{"range radar", "range: 12,34 km", "range: NaN km", "range: 1e999 km", "range: 30 - 20 km", "range: 50 km or more", "power: 5 flops", "range: 5"} {
		terms := querySpecifications(query)
		if query == "range: 5" {
			if len(terms) != 1 || terms[0].matches(ResearchClaim{Field: "range", Status: "known", Value: &ResearchValue{Number: &ResearchNumber{researchFloat(5), researchFloat(5), true, true, "m"}}}) {
				t.Fatal("missing unit became meters")
			}
		} else if len(terms) != 0 {
			t.Fatalf("ambiguous query parsed: %q %+v", query, terms)
		}
	}
}

func TestSpecificationRankingUsesClaimsAndPreservesOlderPolicies(t *testing.T) {
	for _, version := range []string{"bm25-minilm-v1", "bm25-minilm-v2", "bm25-minilm-v3"} {
		f := researchTestFixture(t, false)
		withRanking(f)
		f.manifest.Search.Version = version
		d := f.open(t)
		vector := make([]float32, 384)
		vector[0] = 1
		results, err := d.Search(vector, "Range: 0.3 km", true, Filter{}, 2, "")
		if err != nil {
			t.Fatal(err)
		}
		if version != "bm25-minilm-v3" {
			if results[0].ID != firstID || d.research != nil {
				t.Fatal("legacy ranking changed or loaded research")
			}
			continue
		}
		if results[0].ID != secondID || results[0].NameMatch || results[0].Ranking.SpecificationTerms != 1 {
			t.Fatal(results)
		}
		matches, err := d.TextMatches(results[0])
		if err != nil {
			t.Fatal(err)
		}
		last := matches[len(matches)-1]
		if last.Method != "specification" || last.ClaimID == "" || last.URL != "https://sample.test/1/0" || last.Score != 1 || results[0].Cosine != 0 {
			t.Fatal(last, results[0])
		}
		filtered, err := d.Search(vector, "Range: 0.3 km", true, Filter{Where: []string{"range > 400 m"}}, 2, "")
		if err != nil || len(filtered) != 1 || filtered[0].ID != firstID {
			t.Fatal(filtered, err)
		}
		for _, match := range filtered[0].matches {
			if match.Method == "specification" {
				t.Fatal("filtered claim leaked")
			}
		}
		vectorOnly, err := d.Search(vector, "Range: 0.3 km", false, Filter{}, 2, "")
		if err != nil || vectorOnly[0].ID != firstID || vectorOnly[0].Score != 1 {
			t.Fatal(vectorOnly, err)
		}
	}
}

func TestSpecificationConjunctionAndDuplicateEvidence(t *testing.T) {
	f := researchTestFixture(t, false)
	withRanking(f)
	f.manifest.Search.Version = "bm25-minilm-v3"
	d := f.open(t)
	vector := make([]float32, 384)
	vector[1] = 1
	results, err := d.Search(vector, "Range: 100-200 m; range: 0.5 km; range: 500 m", true, Filter{}, 2, "")
	if err != nil || results[0].ID != firstID || results[0].Ranking.SpecificationTerms != 2 {
		t.Fatal(results, err)
	}
	score := 0.0
	for _, match := range results[0].matches {
		if match.Method == "specification" {
			score += match.Score
		}
	}
	if math.Abs(score-1) > 1e-12 {
		t.Fatal("duplicate numeric clause inflated coverage", score)
	}
	mutateResearchRows(t, f, "research/claims.json", func(rows []map[string]any) { rows[2]["raw"].(map[string]any)["name"] = "False property" })
	d = f.open(t)
	if _, err := d.Search(vector, "False property: 500 m", true, Filter{}, 2, ""); err == nil {
		t.Fatal("claim without matching source fact was used")
	}
}
