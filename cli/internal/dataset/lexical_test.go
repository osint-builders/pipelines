package dataset

import (
	"fmt"
	"math"
	"reflect"
	"strings"
	"testing"
)

func TestLexicalFullTextAndPartialName(t *testing.T) {
	documents := []lexicalDocument{
		{Entity: 0, Text: "Merlin land surveillance radar", Field: "name"},
		{Entity: 0, Text: "A rotating array detects targets at 250 km using an X-band transmitter.", EvidenceID: "specifications", Field: "body"},
		{Entity: 1, Text: "Merlin helicopter", Field: "name"},
		{Entity: 1, Text: "Cruising speed is 250 km per hour.", Field: "body"},
	}
	index := newLexicalIndex(documents)
	for _, query := range []string{"rotating array transmitter", "250 km X-band", "land surveillance"} {
		hits := index.search(query)
		if len(hits) == 0 || hits[0].Entity != 0 || hits[0].Reason != "lexical" {
			t.Fatalf("%q: %+v", query, hits)
		}
	}
	if hit := index.search("rotating array")[0]; hit.Doc != 1 || documents[hit.Doc].EvidenceID != "specifications" {
		t.Fatalf("lost winning evidence: %+v", hit)
	}
	for _, query := range []string{"", "---", "zzqvxx unsupported", "dolphin"} {
		if hits := index.search(query); len(hits) != 0 {
			t.Fatalf("unsupported query %q produced %+v", query, hits)
		}
	}
}

func TestLexicalUnicodeAndDesignations(t *testing.T) {
	documents := []lexicalDocument{
		{Entity: 0, Text: "Ｆ–１６C Café AN/APG-77 12.7 mm", Field: "body"},
		{Entity: 1, Text: "F-18C 127 mm", Field: "body"},
		{Entity: 2, Text: "high-altitude 100-200 km", Field: "body"},
	}
	index := newLexicalIndex(documents)
	for _, query := range []string{"f16c", "F-16C", "cafe", "cafe\u0301", "APG77", "12.7"} {
		hits := index.search(query)
		if len(hits) != 1 || hits[0].Entity != 0 {
			t.Fatalf("%q: %+v", query, hits)
		}
	}
	if hits := index.search("127"); len(hits) != 1 || hits[0].Entity != 1 {
		t.Fatalf("collapsed decimal quantity: %+v", hits)
	}
	for _, query := range []string{"altitude", "100", "200"} {
		if hits := index.search(query); len(hits) != 1 || hits[0].Entity != 2 {
			t.Fatalf("joined ordinary words or numeric range %q: %+v", query, hits)
		}
	}
}

func TestLexicalNameTyposStayWithinNames(t *testing.T) {
	documents := []lexicalDocument{
		{Entity: 0, Text: "Leopard Cheetah F-16", Field: "name"},
		{Entity: 1, Text: "Leopard Cheetah F-16", Field: "body"},
		{Entity: 2, Text: "Leopard Cheetah F-16", Field: "caption"},
		{Entity: 3, Text: "Leopard Cheetah F-16", Field: "description"},
		{Entity: 4, Text: "Leopard Cheetah F-16", Field: "ocr"},
	}
	index := newLexicalIndex(documents)
	for _, query := range []string{"leoprad", "leoprd", "leeopard", "leopart", "leopards"} {
		hits := index.search(query)
		if len(hits) != 1 || hits[0].Entity != 0 || hits[0].Reason != "name_typo" ||
			!reflect.DeepEqual(hits[0].Terms, []string{"leopard"}) {
			t.Fatalf("%q: %+v", query, hits)
		}
	}
	for _, query := range []string{"lepat", "F-17", "cheet"} {
		if hits := index.search(query); len(hits) != 0 {
			t.Fatalf("unbounded typo %q: %+v", query, hits)
		}
	}
	if len(index.search("leopard")) != len(documents) {
		t.Fatal("exact words must search every field")
	}
	if documents[0].Text != "Leopard Cheetah F-16" || len(documents) != 5 {
		t.Fatal("search mutated source names")
	}
}

func TestLexicalFrequencySaturatesAndLengthsNormalize(t *testing.T) {
	index := newLexicalIndex([]lexicalDocument{
		{Entity: 0, Text: "radar noise noise noise", Field: "body"},
		{Entity: 1, Text: "radar radar radar noise", Field: "body"},
		{Entity: 2, Text: "radar " + strings.Repeat("noise ", 30), Field: "body"},
	})
	hits := index.search("radar")
	if len(hits) != 3 || hits[0].Entity != 1 || hits[1].Entity != 0 || hits[2].Entity != 2 {
		t.Fatalf("frequency/length ordering: %+v", hits)
	}
	if hits[0].Score >= 3*hits[1].Score {
		t.Fatal("three occurrences must not triple the score")
	}
	if !reflect.DeepEqual(hits, index.search("radar radar radar")) {
		t.Fatal("repeating query words changed the score")
	}
}

func TestLexicalEvidenceDoesNotAccumulate(t *testing.T) {
	index := newLexicalIndex([]lexicalDocument{
		{Entity: 4, Text: "radar array", Field: "body"},
		{Entity: 2, Text: "radar array", Field: "body"},
		{Entity: 4, Text: "radar array", Field: "caption"},
		{Entity: 4, Text: "radar array", Field: "ocr"},
	})
	hits := index.search("array radar")
	if len(hits) != 2 || hits[0].Entity != 2 || hits[1].Entity != 4 || hits[0].Score != hits[1].Score || hits[1].Doc != 0 {
		t.Fatalf("duplicate evidence added score or changed tie order: %+v", hits)
	}
	for range 20 {
		if !reflect.DeepEqual(hits, index.search("radar array")) {
			t.Fatal("ties, terms, or scores are unstable")
		}
	}
}

func TestLexicalOneQueryTermUsesOneSpellingPerDocument(t *testing.T) {
	index := newLexicalIndex([]lexicalDocument{
		{Entity: 0, Text: "falcon falcons", Field: "name"},
		{Entity: 1, Text: "falcon unrelated", Field: "name"},
	})
	hits := index.search("falcon")
	if len(hits) != 2 || math.Abs(hits[0].Score-hits[1].Score) > 1e-12 || hits[0].Reason != "lexical" || len(hits[0].Terms) != 1 {
		t.Fatalf("a query term accumulated multiple spelling variants: %+v", hits)
	}
}

func TestLexicalEmptyIndex(t *testing.T) {
	for _, documents := range [][]lexicalDocument{nil, {{Text: "...", Field: "name"}}} {
		if hits := newLexicalIndex(documents).search("radar"); len(hits) != 0 {
			t.Fatalf("empty index produced %+v", hits)
		}
	}
}

func BenchmarkLexical(b *testing.B) {
	documents := make([]lexicalDocument, 0, 24000)
	words := make([]string, 5000)
	for i := range words {
		words[i] = fmt.Sprintf("term%d", i)
	}
	for i := range 20000 {
		tokens := make([]string, 140)
		for j := range tokens {
			tokens[j] = words[(i*17+j)%len(words)]
		}
		documents = append(documents, lexicalDocument{Entity: i / 5, Text: strings.Join(tokens, " "), Field: "body"})
		if i%5 == 0 {
			documents = append(documents, lexicalDocument{Entity: i / 5, Text: "Leopard surveillance radar", Field: "name"})
		}
	}
	b.Run("build", func(b *testing.B) {
		b.ReportAllocs()
		for b.Loop() {
			_ = newLexicalIndex(documents)
		}
	})
	index := newLexicalIndex(documents)
	b.Run("query", func(b *testing.B) {
		b.ReportAllocs()
		for b.Loop() {
			_ = index.search("term72 term81 leoprad")
		}
	})
}
