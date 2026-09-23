package dataset

import (
	"fmt"
	"reflect"
	"testing"
)

func TestSearchPagesCoverTheWholePoolWithStableTies(t *testing.T) {
	d := &Dataset{Manifest: Manifest{Model: Model{Dimensions: 384}}, byID: map[string]int{}}
	for i := range 205 {
		id := fmt.Sprintf("sample:%03d", i)
		kind := "radar"
		if i%2 == 1 {
			kind = "sensor"
		}
		d.Entities = append(d.Entities, Entity{ID: id, Kind: kind, Source: "sample"})
		d.byID[id] = i
		d.chunks = append(d.chunks, Chunk{Entity: i, Text: "source text"})
		vector := make([]float32, 384)
		vector[0] = 1
		d.vectors = append(d.vectors, vector...)
	}
	query := make([]float32, 384)
	query[0] = 1
	for _, hybrid := range []bool{false, true} {
		for _, filter := range []Filter{{}, {Kind: "radar"}, {Source: "absent"}} {
			want, err := d.search(query, "", hybrid, filter, len(d.Entities), "")
			if err != nil {
				t.Fatal(err)
			}
			var got []Result
			for number := 1; number <= 8; number++ {
				page, err := d.SearchPage(query, "", hybrid, filter, Page{Number: number, Size: 37})
				if err != nil {
					t.Fatal(err)
				}
				if page.Total != len(want) || page.HasMore != (number*37 < len(want)) || !reflect.DeepEqual(page.Leaders, want[:min(2, len(want))]) {
					t.Fatalf("invalid page metadata: %+v", page)
				}
				got = append(got, page.Results...)
			}
			if len(got) != len(want) {
				t.Fatalf("lost results: %d != %d", len(got), len(want))
			}
			for i := range want {
				if got[i].ID != want[i].ID {
					t.Fatal("ranking changed between pages")
				}
			}
		}
	}
	page, err := d.SearchPage(query, "", false, Filter{}, Page{Number: int(^uint(0) >> 1), Size: 100})
	if err != nil || len(page.Results) != 0 || page.Total != 205 || page.HasMore {
		t.Fatal("out-of-range page overflowed", page, err)
	}
	for _, p := range []Page{{Number: 0, Size: 10}, {Number: -1, Size: 10}, {Number: 1, Size: 0}, {Number: 1, Size: 101}} {
		if _, err := d.SearchPage(query, "", false, Filter{}, p); err == nil {
			t.Fatal("invalid page accepted", p)
		}
	}
}

func TestVisualAndGeneratedPagesPreserveRankingAndLeaders(t *testing.T) {
	d := newObservationFixture(t).open(t)
	text, image := make([]float32, 384), make([]float32, 512)
	text[0], image[0] = 1, 1
	searches := []func(Page) (RankedPage[VisualResult], error){
		func(p Page) (RankedPage[VisualResult], error) {
			return d.SearchImagesPage(image, nil, "", Filter{}, p, false)
		},
		func(p Page) (RankedPage[VisualResult], error) {
			return d.SearchImagesPage(image, text, "label", Filter{}, p, false)
		},
		func(p Page) (RankedPage[VisualResult], error) {
			return d.SearchImagesPage(image, text, "label", Filter{}, p, true)
		},
		func(p Page) (RankedPage[VisualResult], error) {
			return d.SearchObservationsPage(text, "label", false, Filter{}, p)
		},
		func(p Page) (RankedPage[VisualResult], error) {
			return d.SearchObservationsPage(text, "label", true, Filter{}, p)
		},
	}
	for i, search := range searches {
		t.Run(fmt.Sprint(i), func(t *testing.T) {
			all, err := search(Page{Number: 1, Size: 100})
			if err != nil {
				t.Fatal(err)
			}
			if all.Total != 2 {
				t.Fatal(all.Total)
			}
			for number := 1; number <= 3; number++ {
				page, err := search(Page{Number: number, Size: 1})
				if err != nil {
					t.Fatal(err)
				}
				if page.Total != 2 || !reflect.DeepEqual(page.Leaders, all.Leaders) || page.HasMore != (number == 1) {
					t.Fatalf("unstable leaders or count: %+v", page)
				}
				if number <= 2 && !reflect.DeepEqual(page.Results, all.Results[number-1:number]) {
					t.Fatal("page changed ranking/provenance")
				}
				if number == 3 && len(page.Results) != 0 {
					t.Fatal("end page not empty")
				}
			}
		})
	}
	filtered, err := d.SearchImagesPage(image, nil, "", Filter{Kind: "sensor"}, Page{Number: 1, Size: 1}, false)
	if err != nil || filtered.Total != 1 || len(filtered.Results) != 1 || filtered.Results[0].Kind != "sensor" || filtered.HasMore {
		t.Fatal(filtered, err)
	}
}

func TestCalibratedPagesKeepGlobalLeadersIncludingPastTheEnd(t *testing.T) {
	d := calibratedFixture(t).open(t)
	text, image := make([]float32, 384), make([]float32, 512)
	text[0], image[0] = 1, 1
	for _, observations := range []bool{false, true} {
		all, err := d.SearchImagesPage(image, text, "radar", Filter{}, Page{Number: 1, Size: 100}, observations)
		if err != nil {
			t.Fatal(err)
		}
		for number := 1; number <= 3; number++ {
			page, err := d.SearchImagesPage(image, text, "radar", Filter{}, Page{Number: number, Size: 1}, observations)
			if err != nil || !reflect.DeepEqual(page.Leaders, all.Results[:2]) {
				t.Fatal("calibration leaders changed", number, err)
			}
		}
	}
	all, err := d.SearchObservationsPage(text, "radar", true, Filter{}, Page{Number: 1, Size: 100})
	if err != nil {
		t.Fatal(err)
	}
	last, err := d.SearchObservationsPage(text, "radar", true, Filter{}, Page{Number: 3, Size: 1})
	if err != nil || len(last.Results) != 0 || !reflect.DeepEqual(last.Leaders, all.Results[:2]) {
		t.Fatal("generated-text leaders changed", err)
	}
}
