import Array "mo:core/Array";
import Char "mo:core/Char";
import Nat "mo:core/Nat";
import Text "mo:core/Text";

/// Minimal JSON token scanning for Casals text responses (no full parser).
module {
  public let MAX_RESULT_CHARS : Nat = 4096;
  private func dquote() : Char { '\"' };

  private func chars(t : Text) : [Char] {
    Text.toArray(t);
  };

  private func fromChars(cs : [Char]) : Text {
    Text.fromArray(cs);
  };

  private func slice(cs : [Char], start : Nat, len : Nat) : [Char] {
    Array.tabulate<Char>(
      len,
      func(i) { cs[start + i] },
    );
  };

  private func indexOf(hay : [Char], needle : [Char]) : ?Nat {
    if (needle.size() == 0) return ?0;
    var i : Nat = 0;
    label outer while (i + needle.size() <= hay.size()) {
      var ok = true;
      var j : Nat = 0;
      while (j < needle.size()) {
        if (hay[i + j] != needle[j]) {
          ok := false;
          j := needle.size();
        } else {
          j += 1;
        };
      };
      if (ok) return ?i;
      i += 1;
    };
    null;
  };

  public func truncate(t : Text, max : Nat) : Text {
    let cs = chars(t);
    if (cs.size() <= max) t else fromChars(slice(cs, 0, max)) # "…";
  };

  private func isSpace(c : Char) : Bool {
    c == ' ' or c == '\n' or c == '\r' or c == '\t';
  };

  private func skipSpace(cs : [Char], i : Nat) : Nat {
    var j = i;
    label skip while (j < cs.size()) {
      if (isSpace(cs[j])) { j += 1 } else { break skip };
    };
    j;
  };

  private func readLeadingNat(cs : [Char], i : Nat) : ?Nat {
    var j = skipSpace(cs, i);
    var digits = "";
    label digits while (j < cs.size()) {
      let c = cs[j];
      if (Char.isDigit(c)) {
        digits := digits # Char.toText(c);
        j += 1;
      } else {
        break digits;
      };
    };
    if (digits.size() == 0) null else Nat.fromText(digits);
  };

  private func valueAfterKey(t : Text, key : Text) : ?Text {
    let hay = chars(t);
    let needle = chars(key);
    switch (indexOf(hay, needle)) {
      case null null;
      case (?i) {
        let start = skipSpace(hay, i + needle.size());
        if (start >= hay.size() or hay[start] != ':') return null;
        ?fromChars(slice(hay, skipSpace(hay, start + 1), hay.size() - skipSpace(hay, start + 1)));
      };
    };
  };

  /// True when the envelope reports ``{"ok": false, ...}``.
  public func responseNotOk(t : Text) : Bool {
    Text.contains(t, #text "\"ok\": false")
    or Text.contains(t, #text "\"ok\":false")
    or Text.contains(t, #text "\"ok\":  false");
  };

  /// True when ``"failed"`` is present and not ``null``.
  public func failedNonNull(t : Text) : Bool {
    switch (valueAfterKey(t, "\"failed\"")) {
      case null false;
      case (?v) {
        let trimmed = fromChars(slice(chars(v), 0, Nat.min(4, chars(v).size())));
        not (trimmed == "null");
      };
    };
  };

  public func remaining(t : Text) : ?Nat {
    switch (valueAfterKey(t, "\"remaining\"")) {
      case null null;
      case (?v) { readLeadingNat(chars(v), 0) };
    };
  };

  /// Quoted string value after ``"next_plan_hash"`` (or null).
  public func nextPlanHash(t : Text) : ?Text {
    switch (valueAfterKey(t, "\"next_plan_hash\"")) {
      case null null;
      case (?v) {
        let cs = chars(v);
        if (cs.size() >= 4 and fromChars(slice(cs, 0, 4)) == "null") return null;
        if (cs.size() >= 2 and cs[0] == dquote()) {
          var out = "";
          var j : Nat = 1;
          label scan while (j < cs.size()) {
            let c = cs[j];
            if (c == dquote()) { break scan };
            out := out # Char.toText(c);
            j += 1;
          };
          ?out;
        } else {
          null;
        };
      };
    };
  };

  /// Count ``"result": "ok"`` entries in an apply response (applied items).
  public func countAppliedOk(t : Text) : Nat {
    var n : Nat = 0;
    var hay = chars(t);
    let needles = [chars("\"result\": \"ok\""), chars("\"result\":\"ok\"")];
    label scan while (true) {
      var best : ?Nat = null;
      for (needle in needles.vals()) {
        switch (indexOf(hay, needle)) {
          case null {};
          case (?i) {
            switch (best) {
              case null { best := ?i };
              case (?b) { if (i < b) { best := ?i } };
            };
          };
        };
      };
      switch (best) {
        case null { break scan };
        case (?i) {
          n += 1;
          hay := slice(hay, i + 1, hay.size() - i - 1);
        };
      };
    };
    n;
  };
};
