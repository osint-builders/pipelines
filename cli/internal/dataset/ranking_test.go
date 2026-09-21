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
	matches, err := d.TextMatches(results[0])
	if err != nil || matches[1].Reason != "source_caption" || matches[1].EvidenceID != evidence || matches[1].MediaID == "" || d.images != nil {
		t.Fatal(matches, err)
	}
	f.members["search/captions.json"] = []byte(`[{"entity":1,"evidence_id":"unrelated","text":"caption","media_id":"sample:media:aaaaaaaaaaaaaaaaaaaaaaaa"}]`)
	d = f.open(t)
	if _, err = d.Search(vector, "caption", true, Filter{}, 1, ""); err == nil {
		t.Fatal("unrelated caption accepted")
	}
	if _, err = d.Search(vector, "caption", false, Filter{}, 1, ""); err != nil {
		t.Fatal("vector search accessed unused captions", err)
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
