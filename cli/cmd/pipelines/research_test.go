package main

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strings"
	"testing"
	"testing/fstest"

	"github.com/osint-builders/pipelines/cli/internal/dataset"
)

func TestResearchArgumentsFailBeforeDatasetLoad(t *testing.T) {
	tooMany := []string{"compare"}
	for i := 0; i < 21; i++ {
		tooMany = append(tooMany, fmt.Sprintf("sample:id%d", i))
	}
	for _, args := range [][]string{
		{"facts"}, {"facts", ""}, {"facts", "sample:one", "sample:two"},
		{"facts", "--where", "range > 1 km", "sample:one"},
		{"relationships"}, {"relationships", " "},
		{"relationships", "--type", "", "sample:one"},
		{"relationships", "--type", "identical", "sample:one"},
		{"compare"}, {"compare", "sample:one"},
		{"compare", "sample:one", "sample:one"},
		{"compare", "sample:one", " "}, tooMany,
		{"list", "radar"}, {"list", "--limit", "0"}, {"list", "--limit", "101"},
		{"list", "--where", " "}, {"search", "--where", "", "radar"},
		{"similar", "--where", " ", "sample:one"},
	} {
		t.Run(strings.Join(args, " "), func(t *testing.T) {
			err := runWithFiles(context.Background(), args, &bytes.Buffer{}, fstest.MapFS{})
			if err == nil || strings.Contains(err.Error(), "development build") {
				t.Fatalf("invalid arguments reached dataset loading: %v (%v)", args, err)
			}
		})
	}
}

func TestResearchLegacyCommandsDoNotLoadModels(t *testing.T) {
	files := legacyBundle(t)
	var out bytes.Buffer
	if err := runWithFiles(context.Background(), []string{"list"}, &out, files); err != nil {
		t.Fatal(err)
	}
	var listed struct {
		Total   int `json:"total"`
		Results []struct {
			ID string `json:"id"`
		} `json:"results"`
	}
	if err := json.Unmarshal(out.Bytes(), &listed); err != nil || listed.Total != 1 || len(listed.Results) != 1 || listed.Results[0].ID != "sample:one" {
		t.Fatalf("legacy query-free list: %s (%v)", out.String(), err)
	}
	out.Reset()
	if err := runWithFiles(context.Background(), []string{"info"}, &out, files); err != nil {
		t.Fatal(err)
	}
	var info map[string]json.RawMessage
	if err := json.Unmarshal(out.Bytes(), &info); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"research", "research_available"} {
		if _, ok := info[name]; ok {
			t.Fatalf("legacy info gained %s", name)
		}
	}
	for _, args := range [][]string{
		{"facts", "sample:one"},
		{"relationships", "sample:one"},
		{"compare", "sample:one", "sample:two"},
		{"list", "--where", "range >= 1 km"},
		{"search", "--where", "range >= 1 km", "radar"},
		{"search", "--image", "missing.png", "--where", "range >= 1 km"},
		{"similar", "--where", "range >= 1 km", "sample:one"},
	} {
		err := runWithFiles(context.Background(), args, &bytes.Buffer{}, files)
		if err == nil || !strings.Contains(err.Error(), "research") {
			t.Fatalf("unavailable research must fail before model/image loading: %v (%v)", args, err)
		}
	}
}

func researchCommandBundle(t *testing.T) fstest.MapFS {
	t.Helper()
	members := map[string][]byte{}
	put := func(name string, value any) {
		raw, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		members[name] = raw
	}
	fields := []dataset.ResearchField{}
	for _, field := range strings.Fields("manufacturer contractor origin_country designer_country operator_country site_country development_status") {
		fields = append(fields, dataset.ResearchField{Name: field, Kind: "text"})
	}
	for _, field := range strings.Fields("service_entry first_flight launch_date retired publication_date updated_date captured_date") {
		fields = append(fields, dataset.ResearchField{Name: field, Kind: "date"})
	}
	for _, field := range strings.Fields("range detection_range ferry_range ceiling length height width wavelength") {
		fields = append(fields, dataset.ResearchField{Name: field, Kind: "number", Unit: "m"})
	}
	for field, unit := range map[string]string{"mass": "kg", "speed": "m/s", "frequency": "Hz", "pulse_repetition_frequency": "Hz", "power": "W", "crew": "count", "quantity": "count"} {
		fields = append(fields, dataset.ResearchField{Name: field, Kind: "number", Unit: unit})
	}
	sort.Slice(fields, func(i, j int) bool { return fields[i].Name < fields[j].Name })
	index := []dataset.Entity{}
	claims := []dataset.ResearchClaim{}
	pageIDs := []string{}
	chunks := []dataset.Chunk{}
	vectors := make([]byte, 3*384*4)
	for i, name := range []string{"zulu", "alpha", "unknown"} {
		entity := dataset.Entity{ID: "sample:" + name, Source: "sample", Kind: "radar", Title: name,
			URL: "https://sample.test/" + name, Categories: []string{"surveillance"}, Aliases: []string{}}
		index = append(index, entity)
		chunks = append(chunks, dataset.Chunk{Entity: i, Text: name})
		binary.LittleEndian.PutUint32(vectors[(i*384+i)*4:], math.Float32bits(1))
		digest := sha256.Sum256([]byte(entity.URL))
		pageID := hex.EncodeToString(digest[:])[:24]
		pageIDs = append(pageIDs, pageID)
		facts := []dataset.ResearchRawFact{}
		add := func(id, field, label, raw, status string, value *dataset.ResearchValue) {
			fact := dataset.ResearchRawFact{Name: label, Raw: raw, Evidence: entity.URL, Values: []float64{}}
			claims = append(claims, dataset.ResearchClaim{ID: "claim:" + strings.Repeat(id, 24), Entity: i, Field: field,
				EvidenceID: pageID, Locator: dataset.ResearchLocator{Kind: "fact", Index: len(facts)}, Raw: fact, Status: status, Value: value})
			facts = append(facts, fact)
		}
		number := func(minimum, maximum float64) *dataset.ResearchValue {
			return &dataset.ResearchValue{Number: &dataset.ResearchNumber{Min: &minimum, Max: &maximum, MinInclusive: true, MaxInclusive: true, Unit: "m"}}
		}
		if i == 0 {
			add("a", "range", "Range", "2 km", "known", number(2000, 2000))
			first, second := "A & B", "C & D"
			add("c", "manufacturer", "Manufacturer", first, "known", &dataset.ResearchValue{Text: &first})
			add("d", "range", "Range", "Unknown", "unknown", nil)
			add("e", "manufacturer", "Manufacturer", second, "known", &dataset.ResearchValue{Text: &second})
		} else if i == 1 {
			add("b", "range", "Range", "1–3 km", "known", number(1000, 3000))
		}
		html := []byte("<p>Alpha is a variant of Zulu.</p>")
		htmlDigest := sha256.Sum256(html)
		members["html/sample/"+pageID+".html"] = html
		put("entities/sample/"+name+".json", map[string]any{"id": entity.ID, "source": entity.Source, "kind": entity.Kind,
			"facts": facts, "evidence": []any{map[string]any{"id": pageID, "url": entity.URL, "markdown": "Alpha is a variant of Zulu.", "html_sha256": hex.EncodeToString(htmlDigest[:])}}})
	}
	sort.Slice(claims, func(i, j int) bool { return claims[i].ID < claims[j].ID })
	put("index.json", index)
	put("chunks.json", chunks)
	members["vectors.f32"] = vectors
	put("research/claims.json", claims)
	put("research/relations.json", []dataset.ResearchRelation{{ID: "relation:" + strings.Repeat("f", 24), Source: 1, Target: 0,
		Type: "variant_of", Basis: "reviewed_source_evidence", Rationale: "Both archived pages identify the variant.",
		Evidence: []dataset.ResearchReference{{Entity: 0, EvidenceID: pageIDs[0], Quote: "Alpha is a variant of Zulu."},
			{Entity: 1, EvidenceID: pageIDs[1], Quote: "Alpha is a variant of Zulu."}}}})
	hashes := map[string]string{}
	for name, raw := range members {
		digest := sha256.Sum256(raw)
		hashes[name] = hex.EncodeToString(digest[:])
	}
	put("manifest.json", dataset.Manifest{FormatVersion: 2, DatasetID: strings.Repeat("a", 64), Entities: len(index),
		EvidencePages: 3, Chunks: 3, Model: dataset.Model{Dimensions: 384}, Files: hashes,
		Research: &dataset.ResearchManifest{Version: "entity-research-v1", Claims: len(claims), Relations: 1, Fields: fields}})
	var body bytes.Buffer
	archive := zip.NewWriter(&body)
	for name, raw := range members {
		entry, err := archive.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write(raw); err != nil {
			t.Fatal(err)
		}
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	return fstest.MapFS{"data/dataset.zip": {Data: body.Bytes()}}
}

func researchResponse(t *testing.T, files fstest.MapFS, value any, args ...string) {
	t.Helper()
	var out bytes.Buffer
	if err := runWithFiles(context.Background(), args, &out, files); err != nil {
		t.Fatalf("%v: %v", args, err)
	}
	if err := json.Unmarshal(out.Bytes(), value); err != nil {
		t.Fatalf("%v: %s (%v)", args, out.String(), err)
	}
	var envelope struct {
		DatasetID string `json:"dataset_id"`
	}
	if err := json.Unmarshal(out.Bytes(), &envelope); err != nil || envelope.DatasetID != strings.Repeat("a", 64) {
		t.Fatalf("%v lost the dataset ID: %s (%v)", args, out.String(), err)
	}
}

func TestResearchCommandsKeepClaimsAndDirectLinkEvidenceWithoutModels(t *testing.T) {
	files := researchCommandBundle(t)
	var facts struct {
		EntityID string                  `json:"entity_id"`
		Claims   []dataset.ResearchClaim `json:"claims"`
	}
	researchResponse(t, files, &facts, "facts", "sample:zulu")
	if facts.EntityID != "sample:zulu" || len(facts.Claims) != 4 || facts.Claims[2].Status != "unknown" || facts.Claims[2].Value != nil {
		t.Fatalf("source claims were selected or inferred: %+v", facts)
	}
	for _, claim := range facts.Claims {
		if claim.EntityID != facts.EntityID || claim.EvidenceURL != "https://sample.test/zulu" || claim.Raw.Evidence != claim.EvidenceURL {
			t.Fatalf("claim lost provenance: %+v", claim)
		}
	}
	for _, id := range []string{"sample:zulu", "sample:alpha"} {
		var links struct {
			Relationships []dataset.ResearchRelation `json:"relationships"`
		}
		researchResponse(t, files, &links, "relationships", "--type", "variant_of", id)
		if len(links.Relationships) != 1 || links.Relationships[0].SourceID != "sample:alpha" || links.Relationships[0].TargetID != "sample:zulu" {
			t.Fatalf("directed link changed with the queried endpoint: %+v", links)
		}
		for _, evidence := range links.Relationships[0].Evidence {
			if evidence.EntityID == "" || evidence.EvidenceURL == "" || evidence.Quote != "Alpha is a variant of Zulu." {
				t.Fatalf("link lost supporting source: %+v", evidence)
			}
		}
		researchResponse(t, files, &links, "relationships", "--type", "equivalent", id)
		if links.Relationships == nil || len(links.Relationships) != 0 {
			t.Fatalf("relationship type leaked: %+v", links)
		}
	}
	var comparison struct {
		EntityIDs []string                          `json:"entity_ids"`
		Fields    []dataset.ResearchComparisonField `json:"fields"`
	}
	researchResponse(t, files, &comparison, "compare", "sample:unknown", "sample:zulu")
	if len(comparison.Fields) != 29 || len(comparison.EntityIDs) != 2 || comparison.EntityIDs[0] != "sample:unknown" {
		t.Fatalf("comparison changed requested order or catalog: %+v", comparison)
	}
	for _, field := range comparison.Fields {
		if len(field.Entities) != 2 || field.Entities[0].EntityID != "sample:unknown" || field.Entities[0].Status != "unknown" || field.Entities[0].Claims == nil || len(field.Entities[0].Claims) != 0 {
			t.Fatalf("missing field became a fact: %+v", field)
		}
		if field.Field == "manufacturer" || field.Field == "range" {
			if field.Entities[1].Status != "claims" || len(field.Entities[1].Claims) != 2 {
				t.Fatalf("comparison chose a source truth: %+v", field)
			}
		}
	}
	var info struct {
		ResearchAvailable bool                      `json:"research_available"`
		Research          *dataset.ResearchManifest `json:"research"`
	}
	researchResponse(t, files, &info, "info")
	if !info.ResearchAvailable || info.Research == nil || len(info.Research.Fields) != 29 || info.Research.Claims != 5 || info.Research.Relations != 1 {
		t.Fatalf("research catalog missing: %+v", info)
	}
}

func TestResearchListAndSimilarApplyEveryPredicateBeforeLimit(t *testing.T) {
	files := researchCommandBundle(t)
	for _, test := range []struct {
		args  []string
		total int
		ids   string
	}{
		{[]string{"list", "--limit", "1"}, 3, "sample:alpha"},
		{[]string{"list", "--where", "range > 1 km", "--where", "range < 3 km", "--where", "manufacturer = Ａ ＆ Ｂ"}, 1, "sample:zulu"},
		{[]string{"list", "--where", "range >= 1 km", "--where", "range <= 3 km", "--limit", "1"}, 2, "sample:alpha"},
		{[]string{"list", "--where", "range = 2e3 m"}, 1, "sample:zulu"},
		{[]string{"list", "--where", "range != 2 km"}, 0, ""},
		{[]string{"list", "--where", "manufacturer = A & B", "--where", "manufacturer != A & B"}, 0, ""},
		{[]string{"list", "--source", "other", "--where", "range = 2 km"}, 0, ""},
		{[]string{"list", "--kind", "aircraft", "--where", "range = 2 km"}, 0, ""},
		{[]string{"list", "--category", "unknown", "--where", "range = 2 km"}, 0, ""},
		{[]string{"similar", "--where", "range >= 1 km", "--where", "range <= 3 km", "sample:zulu"}, 0, "sample:alpha"},
	} {
		t.Run(strings.Join(test.args, " "), func(t *testing.T) {
			var response struct {
				Total   int              `json:"total"`
				Results []dataset.Entity `json:"results"`
			}
			researchResponse(t, files, &response, test.args...)
			ids := []string{}
			for _, entity := range response.Results {
				ids = append(ids, entity.ID)
			}
			if response.Results == nil || strings.Join(ids, ",") != test.ids || response.Total != test.total {
				t.Fatalf("incorrect eligibility or total: %+v", response)
			}
		})
	}
}

func TestResearchInvalidPredicatesFailBeforeEveryModelPath(t *testing.T) {
	files := researchCommandBundle(t)
	for _, start := range [][]string{{"list"}, {"search"}, {"search", "--mode", "vector"}, {"search", "--image", "missing.png"}, {"search", "--observations"}, {"search", "--observations", "--image", "missing.png"}, {"similar"}} {
		args := append(append([]string{}, start...), "--where", "range > 5 Hz")
		if start[0] == "search" {
			args = append(args, "radar")
		} else if start[0] == "similar" {
			args = append(args, "sample:zulu")
		}
		err := runWithFiles(context.Background(), args, &bytes.Buffer{}, files)
		if err == nil || !strings.Contains(err.Error(), "unit") {
			t.Fatalf("invalid dimension reached a model or image path: %v (%v)", args, err)
		}
	}
}
