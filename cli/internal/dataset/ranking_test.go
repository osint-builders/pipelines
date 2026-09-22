package dataset

import (
	"encoding/json"
	"math"
	"strings"
	"testing"
)

func withRanking(f *imageBundleFixture) {
	f.manifest.Search = &SearchPolicy{Version: "bm25-minilm-v1", K1: 1.2, B: .75, RankConstant: 60, LexicalWeight: 2, SemanticWeight: 1}
	f.members["search/captions.json"] = []byte(`[]`)
	var chunks []Chunk
	_ = json.Unmarshal(f.members["chunks.json"], &chunks)
	chunks[2].EvidenceID = digestString([]byte("https://sample.test/1/0"))[:24]
	f.members["chunks.json"], _ = json.Marshal(chunks)
}

func TestHybridLexicalFusionAndVectorCompatibility(t *testing.T) {
	f := newImageFixture(t)
	withRanking(f)
	d := f.open(t)
	vector := make([]float32, 384)
	vector[0] = 1
	results, err := d.Search(vector, "precipitation", true, Filter{}, 2, "")
	if err != nil || results[0].ID != secondID || results[0].NameMatch {
		t.Fatal(results, err)
	}
	first := results[0]
	if first.Ranking.SemanticRank != 2 || first.Ranking.LexicalRank != 1 || math.Abs(first.Score-(1.0/62+2.0/61)) > 1e-12 {
		t.Fatal(first)
	}
	matches, err := d.TextMatches(first)
	if err != nil || len(matches) != 2 || matches[0].Method != "semantic" || matches[1].Method != "lexical" || matches[1].Reason != "source_text" || matches[1].URL == "" {
		t.Fatal(matches, err)
	}
	vectorResults, err := d.Search(vector, "precipitation", false, Filter{}, 2, "")
	if err != nil || vectorResults[0].ID != firstID || vectorResults[0].Ranking != nil || vectorResults[0].Score != 1 {
		t.Fatal(vectorResults, err)
	}
	if d.images != nil || d.observations != nil {
		t.Fatal("source ranking loaded optional models")
	}
	filtered, err := d.Search(vector, "precipitation", true, Filter{Kind: "sensor"}, 1, "")
	if err != nil || filtered[0].Ranking.SemanticRank != 1 || filtered[0].Ranking.LexicalRank != 1 {
		t.Fatal(filtered, err)
	}
	missing, err := d.Search(vector, "precipitation", true, Filter{Source: "missing"}, 1, "")
	if err != nil || len(missing) != 0 {
		t.Fatal(missing, err)
	}
}

func TestExactAndMisspelledQueryWordsReportDistinctMatchedTerms(t *testing.T) {
	index := newLexicalIndex([]lexicalDocument{{Entity: 0, Text: "Falcon", Field: "name"}})
	hits := index.search("falcon falconn")
	if len(hits) != 1 || len(hits[0].Terms) != 1 || hits[0].Terms[0] != "falcon" {
		t.Fatal(hits)
	}
}

func TestSourceNamesWinWithoutPromotingIncidentalOrNumericMatches(t *testing.T) {
	f := newImageFixture(t)
	withRanking(f)
	d := f.open(t)
	vector := make([]float32, 384)
	vector[1] = 1
	results, err := d.Search(vector, "russian cheeseboard", true, Filter{}, 2, "")
	if err != nil || results[0].ID != firstID || !results[0].NameMatch || results[0].Score != 2 {
		t.Fatal(results, err)
	}
	for _, query := range []string{"F-160", "F-16A", "prefixfalconsuffix"} {
		if sourceNameMatch(query, Entity{Title: "F-16", Aliases: []string{"Falcon"}}) {
			t.Fatal("variant became exact name", query)
		}
	}
	if !sourceNameMatch("F 16 fighter", Entity{Title: "F-16"}) {
		t.Fatal("designation punctuation lost")
	}
	if !sourceNameMatch("Angara 1 2", Entity{Title: "Angara 1.2"}) || !sourceNameMatch("Meteor", Entity{Title: "Météor"}) {
		t.Fatal("source-name normalization lost punctuation or accents")
	}
	results, err = d.Search(vector, "air defense", true, Filter{}, 2, "")
	if err != nil || results[0].NameMatch || results[1].NameMatch {
		t.Fatal("body became alias", results, err)
	}
}

func TestVersionTwoNamesRequireAnExplicitQuerySubject(t *testing.T) {
	for _, item := range []struct {
		query string
		name  string
		want  bool
	}{
		{"AIR", "AIR", true},
		{"MAX", "MAX", true},
		{"Armor", "Armor", true},
		{"air surveillance radar", "AIR", false},
		{"vehicle; Max Range: 550 kilometer; Combat Weight: 55,000 kilogram", "MAX", false},
		{"a main battle tank with improved armor protection than Leopard 1 in the Italian Army", "Armor", false},
		{"operating in the X band low observable radar installed in the US Air Force B-1B Lancer aircraft", "AIR", false},
		{"a command and control system integrated onto the BAZ-6909 8x8 truck and associated with S-350", "BAZ6909", false},
		{"a command and control system integrated onto the BAZ-6909 8x8 truck and associated with S-350", "S-350", false},
		{"a main battle tank with improved protection than Leopard 1 in the Italian Army", "Leopard 1", false},
		{"American M1A2 Abrams main battle tank", "M1A2", true},
		{"M142 HIMARS wheeled rocket artillery launcher", "M142 HIMARS", true},
		{"Russian T-90M main battle tank", "T-90M", true},
		{"1B75 acoustic counter battery sensor", "1B75", true},
		{"F 16 fighter", "F-16", true},
		{"F-160", "F-16", false},
		{"F-16A", "F-16", false},
		{"prefixfalconsuffix", "Falcon", false},
		{"russian cheeseboard", "Cheese Board", true},
		{"Angara 1 2", "Angara 1.2", true},
		{"Meteor", "Météor", true},
		{"medium mine protected vehicle", "Medium Mine Protected Vehicle", true},
		{`"AIR" radar`, "AIR", true},
		{`find "MAX" radar`, "MAX", true},
		{`"find" AIR "radar"`, "AIR", false},
		{`"Russian" Armor "protection"`, "Armor", false},
		{"“Météor” missile", "Météor", true},
		{"«Armor» radar", "Armor", true},
		{`a radar mounted on the "Lancer" aircraft`, "Lancer", false},
		{"", "AIR", false},
	} {
		t.Run(item.query+"/"+item.name, func(t *testing.T) {
			entity := Entity{Title: "Unrelated title", Aliases: []string{item.name}}
			if got := sourceNameMatchV2(item.query, entity); got != item.want {
				t.Fatalf("name priority %v, want %v", got, item.want)
			}
		})
	}
}

func TestRankingVersionPreservesLegacyAndKeepsIncidentalNameAsOrdinaryEvidence(t *testing.T) {
	for _, version := range []string{"bm25-minilm-v1", "bm25-minilm-v2"} {
		t.Run(version, func(t *testing.T) {
			f := newImageFixture(t)
			withRanking(f)
			f.manifest.Search.Version = version
			var entities []Entity
			_ = json.Unmarshal(f.members["index.json"], &entities)
			entities[0].Title, entities[0].Aliases = "AIR", []string{"AIR"}
			entities[1].Title, entities[1].Aliases = "AN/APQ-164", []string{"AN/APQ-164"}
			f.members["index.json"], _ = json.Marshal(entities)
			var chunks []Chunk
			_ = json.Unmarshal(f.members["chunks.json"], &chunks)
			chunks[2].Text = "low observable air navigation radar"
			f.members["chunks.json"], _ = json.Marshal(chunks)
			d := f.open(t)
			vector := make([]float32, 384)
			vector[1] = 1
			results, err := d.Search(vector, "low observable air navigation radar", true, Filter{}, 2, "")
			if err != nil {
				t.Fatal(err)
			}
			if version == "bm25-minilm-v1" {
				if results[0].ID != firstID || !results[0].NameMatch {
					t.Fatal("legacy ranking changed", results)
				}
			} else if results[0].ID != secondID || results[0].NameMatch || results[1].NameMatch || results[1].Ranking.LexicalRank == 0 {
				t.Fatal("incidental alias either dominated or lost ordinary lexical evidence", results)
			}
			exact, err := d.Search(vector, "AIR", true, Filter{}, 2, "")
			if err != nil || exact[0].ID != firstID || !exact[0].NameMatch {
				t.Fatal("exact name priority lost", exact, err)
			}
		})
	}
}

func TestSourceCaptionsHaveTheirOwnEvidenceAndDoNotLoadImages(t *testing.T) {
	f := newImageFixture(t)
	withRanking(f)
	evidence := digestString([]byte("https://sample.test/1/0"))[:24]
	f.manifest.Search.Captions = 1
	f.members["search/captions.json"], _ = json.Marshal([]sourceCaption{{Entity: 1, EvidenceID: evidence, Text: "A parabolic dish alongside a pedestal", MediaID: "sample:media:" + strings.Repeat("a", 24)}})
	d := f.open(t)
	vector := make([]float32, 384)
	vector[0] = 1
	results, err := d.Search(vector, "parabolic pedestal", true, Filter{}, 1, "")
	if err != nil || results[0].ID != secondID {
		t.Fatal(results, err)
	}
	if len(d.evidenceURLs) != 0 {
		t.Fatal("loaded caption provenance before selecting results")
	}
	matches, err := d.TextMatches(results[0])
	if err != nil || matches[1].Reason != "source_caption" || matches[1].EvidenceID != evidence || matches[1].MediaID == "" || d.images != nil {
		t.Fatal(matches, err)
	}
	f.members["search/captions.json"] = []byte(`[{"entity":1,"evidence_id":"unrelated","text":"caption","media_id":"sample:media:aaaaaaaaaaaaaaaaaaaaaaaa"}]`)
	d = f.open(t)
	results, err = d.Search(vector, "caption", true, Filter{}, 1, "")
	if err != nil {
		t.Fatal(err)
	}
	if _, err = d.TextMatches(results[0]); err == nil {
		t.Fatal("unrelated caption accepted")
	}
	if _, err = d.Search(vector, "caption", false, Filter{}, 1, ""); err != nil {
		t.Fatal("vector search accessed unused captions", err)
	}
	var chunks []Chunk
	_ = json.Unmarshal(f.members["chunks.json"], &chunks)
	chunks[2].EvidenceID = ""
	f.members["chunks.json"], _ = json.Marshal(chunks)
	if err := f.open(t).Verify(); err == nil || !strings.Contains(err.Error(), "caption references unrelated evidence") {
		t.Fatal("verification failed to inspect every caption", err)
	}
}

func TestObservationLexicalProvenanceAndCombinedTwoStageFusion(t *testing.T) {
	f := newObservationFixture(t)
	withRanking(f)
	d := f.open(t)
	vector := make([]float32, 384)
	vector[0] = 1
	results, err := d.SearchObservations(vector, "Маркировка", true, Filter{}, 2)
	if err != nil || results[0].ID != secondID || results[0].NameMatch {
		t.Fatal(results, err)
	}
	match := results[0].Matches[1]
	if match.Channel != "ocr" || match.Method != "lexical" || match.ObservationID == "" || match.RecipeSHA256 == "" || match.Origin != "generated" || match.URL == "" {
		t.Fatal(match)
	}
	if d.lexical[0] != nil {
		t.Fatal("duplicated source lexical index")
	}
	image := make([]float32, 512)
	image[0] = 1
	combined, err := d.SearchImagesWithObservations(image, vector, "Маркировка", Filter{}, 2)
	if err != nil || len(combined) != 2 {
		t.Fatal(combined, err)
	}
	for _, item := range combined {
		if math.Abs(item.Score-(1.0/61+1.0/62)) > 1e-12 {
			t.Fatal("extra text channel counted in image fusion", item)
		}
	}
	plain, err := d.Search(vector, "Маркировка", true, Filter{}, 2, "")
	if err != nil || plain[0].ID != firstID || plain[0].Ranking.LexicalRank != 0 {
		t.Fatal("generated text leaked into default", plain, err)
	}
}

func TestVerifyChecksCaptionEvidenceURLsWithoutEagerSearchReads(t *testing.T) {
	for _, url := range []string{"https://valid.test/page", "ftp://invalid.test/page", "https://user@invalid.test/page"} {
		t.Run(url, func(t *testing.T) {
			f := newImageFixture(t)
			withRanking(f)
			f.manifest.FormatVersion = 2
			f.manifest.Image = nil
			for name := range f.members {
				if strings.HasPrefix(name, "image/") {
					delete(f.members, name)
				}
			}
			name := "entities/" + strings.ReplaceAll(firstID, ":", "/") + ".json"
			var record map[string]any
			_ = json.Unmarshal(f.members[name], &record)
			page := record["evidence"].([]any)[0].(map[string]any)
			oldID := page["id"].(string)
			newID := digestString([]byte(url))[:24]
			page["id"], page["url"] = newID, url
			f.members["html/sample/"+newID+".html"] = f.members["html/sample/"+oldID+".html"]
			delete(f.members, "html/sample/"+oldID+".html")
			f.members[name], _ = json.Marshal(record)
			var chunks []Chunk
			_ = json.Unmarshal(f.members["chunks.json"], &chunks)
			chunks[2].EvidenceID = ""
			f.members["chunks.json"], _ = json.Marshal(chunks)
			f.manifest.Search.Captions = 1
			f.members["search/captions.json"], _ = json.Marshal([]sourceCaption{{Entity: 0, EvidenceID: newID, Text: "caption", MediaID: "sample:media:" + strings.Repeat("a", 24)}})
			err := f.open(t).Verify()
			if (err == nil) != (url == "https://valid.test/page") {
				t.Fatal(url, err)
			}
		})
	}
}
