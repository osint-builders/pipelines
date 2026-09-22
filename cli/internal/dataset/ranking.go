package dataset

import (
	"bytes"
	"encoding/json"
	"errors"
	"math"
	"regexp"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"
)

var captionMediaID = regexp.MustCompile(`^[a-z0-9_-]+:media:[a-f0-9]{24}$`)

type SearchPolicy struct {
	Version        string  `json:"version"`
	K1             float64 `json:"k1"`
	B              float64 `json:"b"`
	RankConstant   int     `json:"rank_constant"`
	LexicalWeight  float64 `json:"lexical_weight"`
	SemanticWeight float64 `json:"semantic_weight"`
	Captions       int     `json:"captions"`
}

// TextRanking reports retrieval signals, never identity probabilities.
type TextRanking struct {
	Method       string  `json:"method"`
	SemanticRank int     `json:"semantic_rank"`
	LexicalRank  int     `json:"lexical_rank,omitempty"`
	TextScore    float64 `json:"text_score"`
}

type sourceCaption struct {
	Entity     int    `json:"entity"`
	EvidenceID string `json:"evidence_id"`
	Text       string `json:"text"`
	MediaID    string `json:"media_id"`
}

type textIndex struct {
	documents []lexicalDocument
	matches   []Match
	index     *lexicalIndex
}

// PrepareTextSearch builds the shared text index before allocating a query model.
func (d *Dataset) PrepareTextSearch(observations bool) error {
	if d.Manifest.Search == nil {
		return nil
	}
	_, err := d.loadTextIndex(observations)
	return err
}

func (d *Dataset) validateSearchPolicy() error {
	p := d.Manifest.Search
	if p == nil {
		for name := range d.members {
			if strings.HasPrefix(name, "search/") {
				return errors.New("search member without search policy")
			}
		}
		return nil
	}
	if (p.Version != "bm25-minilm-v1" && p.Version != "bm25-minilm-v2") || p.K1 != 1.2 || p.B != .75 || p.RankConstant < 1 || p.RankConstant > 1000 ||
		!(p.LexicalWeight > 0 && p.LexicalWeight <= 10) || !(p.SemanticWeight > 0 && p.SemanticWeight <= 10) || p.Captions < 0 || p.Captions > 100000 {
		return errors.New("unsupported text search policy")
	}
	for name := range d.members {
		if strings.HasPrefix(name, "search/") && name != "search/captions.json" {
			return errors.New("unknown search member")
		}
	}
	if d.members["search/captions.json"] == nil || d.Manifest.Files["search/captions.json"] == "" {
		return errors.New("missing caption index")
	}
	return nil
}

func (d *Dataset) loadTextIndex(observations bool) (*textIndex, error) {
	slot := 0
	if observations {
		slot = 1
	}
	if d.lexical[slot] != nil {
		return d.lexical[slot], nil
	}
	if err := d.LoadVectors(); err != nil {
		return nil, err
	}
	index := &textIndex{}
	add := func(document lexicalDocument, match Match) {
		if strings.TrimSpace(document.Text) == "" {
			return
		}
		index.documents = append(index.documents, document)
		index.matches = append(index.matches, match)
	}
	for i, entity := range d.Entities {
		for _, name := range append([]string{entity.Title}, entity.Aliases...) {
			add(lexicalDocument{Entity: i, Text: name, Field: "name"}, Match{Channel: "text", Reason: "source_name"})
		}
	}
	for _, chunk := range d.chunks {
		if chunk.EvidenceID == "" {
			continue
		}
		text := chunk.Text
		if _, body, ok := strings.Cut(text, "\n"); ok {
			text = body
		}
		add(lexicalDocument{Entity: chunk.Entity, Text: text, EvidenceID: chunk.EvidenceID, Field: "body"}, Match{Channel: "text", EvidenceID: chunk.EvidenceID, Reason: "source_text"})
	}
	raw, err := d.readImage("search/captions.json", 32<<20)
	if err != nil {
		return nil, err
	}
	var captions []sourceCaption
	if err := json.Unmarshal(raw, &captions); err != nil {
		return nil, err
	}
	if captions == nil || len(captions) != d.Manifest.Search.Captions {
		return nil, errors.New("caption count mismatch")
	}
	var rawCaptions []json.RawMessage
	if err := json.Unmarshal(raw, &rawCaptions); err != nil {
		return nil, err
	}
	for _, row := range rawCaptions {
		fields, err := rawObject(row)
		if err != nil || !hasFields(fields, "entity", "evidence_id", "text", "media_id") {
			return nil, errors.New("invalid source caption fields")
		}
		for _, value := range fields {
			if bytes.Equal(value, []byte("null")) {
				return nil, errors.New("null source caption field")
			}
		}
	}
	var previous string
	previousEntity := -1
	for _, caption := range captions {
		if caption.Entity < 0 || caption.Entity >= len(d.Entities) || len(caption.Text) > 16384 || !utf8.ValidString(caption.Text) || strings.TrimSpace(caption.Text) != caption.Text || caption.Text == "" ||
			!strings.HasPrefix(caption.MediaID, d.Entities[caption.Entity].Source+":media:") || !captionMediaID.MatchString(caption.MediaID) {
			return nil, errors.New("invalid source caption")
		}
		identity := caption.EvidenceID + "\x00" + caption.Text
		if caption.Entity < previousEntity || (caption.Entity == previousEntity && identity <= previous) {
			return nil, errors.New("duplicate or unordered source caption")
		}
		previous, previousEntity = identity, caption.Entity
		if !safeKey(caption.EvidenceID) {
			return nil, errors.New("invalid caption evidence ID")
		}
		add(lexicalDocument{Entity: caption.Entity, Text: caption.Text, EvidenceID: caption.EvidenceID, Field: "caption"}, Match{Channel: "text", EvidenceID: caption.EvidenceID, MediaID: caption.MediaID, Reason: "source_caption"})
	}
	if observations {
		if err := d.LoadObservations(); err != nil {
			return nil, err
		}
		for _, chunk := range d.observations.chunks {
			row := d.observations.rows[d.observations.byID[chunk.ObservationID]]
			for _, ref := range row.References {
				add(lexicalDocument{Entity: d.byID[ref.EntityID], Text: chunk.Text, EvidenceID: ref.EvidenceID, Field: row.Kind}, d.observationMatch(row, ref))
			}
		}
	}
	index.index = newLexicalIndex(index.documents)
	d.lexical[slot] = index
	return index, nil
}

func (d *Dataset) observationMatch(row Observation, ref ObservationReference) Match {
	recipe := d.observations.provenance[row.RecipeSHA256]
	return Match{Channel: row.Kind, Origin: "generated", ObservationID: row.ID, MediaID: ref.MediaID, EvidenceID: ref.EvidenceID,
		URL: d.images.evidence[d.byID[ref.EntityID]][ref.EvidenceID], RecipeSHA256: row.RecipeSHA256, ModelID: recipe.ModelID,
		ModelRevision: recipe.ModelRevision, EmbeddingModelSHA256: d.Manifest.Observations.EmbeddingModelSHA256, Reason: row.Kind}
}

// Source names match whole normalized query spans. A numeric suffix cannot turn
// one equipment designation into an exact match for another variant.
func sourceNameMatch(query string, entity Entity) bool {
	words := lexicalTokens(query)
	names := map[string]bool{}
	for _, name := range append([]string{entity.Title}, entity.Aliases...) {
		if normalized := key(strings.Join(lexicalTokens(name), "")); normalized != "" {
			names[normalized] = true
		}
	}
	for i := range words {
		joined := ""
		for _, word := range words[i:] {
			joined += key(word)
			if names[joined] {
				return true
			}
		}
	}
	return false
}

// Version 2 reserves overriding name priority for an explicit query subject.
// Other name occurrences still contribute to lexical and semantic retrieval.
func sourceNameMatchV2(query string, entity Entity) bool {
	words := lexicalTokens(query)
	whole := key(strings.Join(words, ""))
	if whole == "" {
		return false
	}
	names := map[string]bool{}
	for _, name := range append([]string{entity.Title}, entity.Aliases...) {
		tokens := lexicalTokens(name)
		normalized := key(strings.Join(tokens, ""))
		if normalized == "" {
			continue
		}
		if normalized == whole {
			return true
		}
		var letter, digit bool
		for _, r := range normalized {
			letter = letter || unicode.IsLetter(r)
			digit = digit || unicode.IsDigit(r)
		}
		names[normalized] = names[normalized] || len(tokens) > 1 || letter && digit
	}
	for i := 0; i < len(words) && i < 2; i++ {
		joined := ""
		for _, word := range words[i:] {
			joined += key(word)
			if names[joined] {
				return true
			}
		}
	}
	// Quoting permits a single-word subject without promoting that same word
	// inside an unquoted description or a later related-entity mention.
	consumedUntil := 0
	for start, opening := range query {
		if start < consumedUntil {
			continue
		}
		var closing rune
		switch opening {
		case '"':
			closing = '"'
		case '“':
			closing = '”'
		case '«':
			closing = '»'
		default:
			continue
		}
		if len(lexicalTokens(query[:start])) > 1 {
			return false
		}
		body := query[start+utf8.RuneLen(opening):]
		if end := strings.IndexRune(body, closing); end >= 0 {
			consumedUntil = start + utf8.RuneLen(opening) + end + utf8.RuneLen(closing)
			_, matched := names[key(strings.Join(lexicalTokens(body[:end]), ""))]
			if matched {
				return true
			}
		}
	}
	return false
}

func (d *Dataset) rankText(results []Result, generated map[string]Match, query string, observations bool) ([]Result, error) {
	index, err := d.loadTextIndex(observations)
	if err != nil {
		return nil, err
	}
	p := d.Manifest.Search
	sort.Slice(results, func(i, j int) bool {
		if results[i].Cosine == results[j].Cosine {
			return results[i].ID < results[j].ID
		}
		return results[i].Cosine > results[j].Cosine
	})
	positions := map[int]int{}
	for i := range results {
		result := &results[i]
		positions[d.byID[result.ID]] = i
		result.Score = p.SemanticWeight / float64(p.RankConstant+i+1)
		result.Ranking = &TextRanking{Method: p.Version, SemanticRank: i + 1}
		match, ok := generated[result.ID]
		if !ok {
			match = Match{Channel: "text", EvidenceID: result.EvidenceID, Reason: "source_text"}
		}
		match.Method, match.Score = "semantic", result.Cosine
		if match.Reason == "" {
			match.Reason = match.Channel
		}
		result.matches = []Match{match}
	}
	rank := 0
	for _, hit := range index.index.search(query) {
		position, ok := positions[hit.Entity]
		if !ok {
			continue
		}
		rank++
		result := &results[position]
		result.Score += p.LexicalWeight / float64(p.RankConstant+rank)
		result.Ranking.LexicalRank = rank
		match := index.matches[hit.Doc]
		match.Method, match.Score, match.Terms = "lexical", hit.Score, hit.Terms
		if hit.Reason == "name_typo" {
			match.Reason = "name_typo"
		}
		result.matches = append(result.matches, match)
		if p.LexicalWeight/float64(p.RankConstant+rank) >= p.SemanticWeight/float64(p.RankConstant+result.Ranking.SemanticRank) {
			result.Snippet, result.EvidenceID = index.documents[hit.Doc].Text, match.EvidenceID
		}
	}
	for i := range results {
		result := &results[i]
		if p.Version == "bm25-minilm-v2" {
			result.NameMatch = sourceNameMatchV2(query, result.Entity)
		} else {
			result.NameMatch = sourceNameMatch(query, result.Entity)
		}
		if result.NameMatch {
			result.Score = 2 + math.Max(-1, math.Min(1, result.Cosine))
		}
		result.Ranking.TextScore = result.Score
	}
	sort.Slice(results, func(i, j int) bool {
		if results[i].NameMatch != results[j].NameMatch {
			return results[i].NameMatch
		}
		if results[i].Score == results[j].Score {
			return results[i].ID < results[j].ID
		}
		return results[i].Score > results[j].Score
	})
	return results, nil
}

func (d *Dataset) TextMatches(result Result) ([]Match, error) {
	if result.Ranking == nil {
		match, err := d.TextMatch(result)
		return []Match{match}, err
	}
	matches := append([]Match(nil), result.matches...)
	for i := range matches {
		if matches[i].URL != "" {
			continue
		}
		resolved, err := d.TextMatch(Result{Entity: result.Entity, EvidenceID: matches[i].EvidenceID, Score: matches[i].Score})
		if err != nil {
			return nil, err
		}
		matches[i].EvidenceID, matches[i].URL = resolved.EvidenceID, resolved.URL
	}
	return matches, nil
}
