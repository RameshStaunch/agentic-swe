package todo

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestCreateAndList(t *testing.T) {
	srv := httptest.NewServer(Handler(&Store{}))
	defer srv.Close()

	res, err := http.Post(srv.URL+"/todos", "application/json", strings.NewReader(`{"title":"write tests"}`))
	if err != nil || res.StatusCode != http.StatusCreated {
		t.Fatalf("create: %v %v", err, res.Status)
	}
	res, _ = http.Get(srv.URL + "/todos")
	var todos []Todo
	json.NewDecoder(res.Body).Decode(&todos)
	if len(todos) != 1 || todos[0].Title != "write tests" {
		t.Fatalf("list: %+v", todos)
	}
}
