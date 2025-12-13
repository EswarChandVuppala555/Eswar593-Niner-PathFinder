import chardet
import pandas as pd
import os
import re

# Define credit-hour bucket options - included in object for consistency
credits_options = ["", "None yet!", "Up to 29 (Freshman)", "30 to 59 (Sophomore)", "60 to 89 (Junior)", "90 to 119 (Senior)", "120 to 149 (5th year)", "150 or more (Super Senior)"]


class Catalog_Menu_Options_Loader:
    """
    Builds two structures from ug_cat_metadata.csv:

    1) year_degree_major_conc_options  (backward compatible)
       { year: { degree_major_code_or_label: [concentration, ...] } }

    2) year_degree_major_conc_tree     (new, drives two dropdowns)
       { year: { degree_level: { major_name: [concentration, ...] } } }

    Optionally reads program_code_map.csv if present to map codes like 'BSEE'
    to (degree_level='Bachelors', major_name='Electrical Engineering').
    """
    def __init__(self):
        metadata_file_path = 'rag_corpus/ug_cat/ug_cat_metadata.csv'
        mapping_file_path = 'rag_corpus/ug_cat/program_code_map.csv'  # optional

        # Detect encoding and read metadata CSV
        with open(metadata_file_path, 'rb') as f:
            result = chardet.detect(f.read(10000))
            encoding = result['encoding'] or 'utf-8'
            print(f"[menu_options] Detected encoding for metadata: {encoding}")

        self.df = pd.read_csv(metadata_file_path, encoding=encoding)
        # Limit to type == 'major'
        self.df = self.df[self.df['type'] == 'major'].copy()
        print(f"Loaded {len(self.df)} rows from course_catalog.csv")

        # Back-compat mapping
        self.year_degree_major_conc_options = {}

        # New nested tree
        self.year_degree_major_conc_tree = {}

        # Optional mapping file (degree_major_code -> degree_level, major_name)
        self.code_map = self._load_program_code_map(mapping_file_path)

        # Build both structures
        self._build_structures()

    # ---------------- helpers ----------------

    def get_courses_for_program(self, year, degree_major, concentration=""):
        """
        Returns a list of courses for a specific catalog year, degree_major, and concentration.
        Only returns rows of type='course'.
        """
        df_filtered = self.df[
            (self.df["catalog_year"] == year) &
            (self.df["degree_major"] == degree_major)
        ]
        if concentration:
            df_filtered = df_filtered[df_filtered["concentration"] == concentration]

        df_filtered = df_filtered[df_filtered["type"] == "course"]

        # Return unique course names
        col = next((c for c in df_filtered.columns if "course" in c.lower()), None)
        if col:
            return df_filtered[col].dropna().unique().tolist()
        else:
            print(f"⚠️ No course column found in data: {df_filtered.columns}")
            return []

    def _load_program_code_map(self, path: str):
        """
        If a mapping file exists, read columns:
        - degree_major (code or label as in metadata)
        - degree_level (e.g., 'Bachelors', 'Masters', 'PhD')
        - major_name   (e.g., 'Electrical Engineering')
        """
        if os.path.exists(path):
            try:
                map_df = pd.read_csv(path)
                required = {'degree_major', 'degree_level', 'major_name'}
                if not required.issubset(set(map_df.columns)):
                    print(f"[menu_options] Mapping file present but missing columns {required - set(map_df.columns)}")
                    return {}
                mapping = {}
                for _, row in map_df.iterrows():
                    dm = str(row['degree_major']).strip()
                    mapping[dm] = (
                        str(row['degree_level']).strip(),
                        str(row['major_name']).strip()
                    )
                print(f"[menu_options] Loaded {len(mapping)} program code mappings.")
                return mapping
            except Exception as e:
                print(f"[menu_options] Failed to read program_code_map.csv: {e}")
                return {}
        else:
            print("[menu_options] No program_code_map.csv found; falling back to heuristics.")
            return {}

    def _heuristic_split(self, degree_major: str):
        """
        Best-effort split when no explicit mapping exists.
        Tries patterns like 'BS-Computer Science', 'BS in Computer Science',
        else tries to decode codes that start with BS/BA/MS/MA/PhD.
        """
        label = str(degree_major).strip()

        # Pattern: "Degree - Major" or "Degree: Major"
        m = re.match(r'^\s*([^:–\-]+)\s*[:–\-]\s*(.+)$', label)
        if m:
            return m.group(1).strip(), m.group(2).strip()

        # Pattern: "Degree in Major"
        m = re.match(r'^\s*(.+?)\s+in\s+(.+)$', label, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip(), m.group(2).strip()

        # Code-like labels, e.g., BSEE, BSCM, BSCPE, MSEE, MSCS, etc.
        # Map leading token to degree level; rest is major (we'll leave as code-y if unknown).
        upper = label.upper()
        if upper.startswith("BSC") or upper.startswith("BS"):
            return "Bachelors", label[2:].strip()  # e.g., 'EE' or 'CPE' → still cryptic but separated
        if upper.startswith("BA"):
            return "Bachelors", label[2:].strip()
        if upper.startswith("MS"):
            return "Masters", label[2:].strip()
        if upper.startswith("MA"):
            return "Masters", label[2:].strip()
        if upper.startswith("PHD") or upper.startswith("PH.D"):
            return "PhD", label[3:].strip()

        # Fallback: can't split confidently—treat whole as major under 'Other'
        return "Other", label

    # ---------------- builders ----------------

    def _build_structures(self):
        catalog_years = sorted(self.df['catalog_year'].dropna().unique())
        print(f"[menu_options] Found {len(catalog_years)} unique catalog years: {catalog_years}")

        for year in catalog_years:
            self.year_degree_major_conc_options[year] = {}
            self.year_degree_major_conc_tree[year] = {}

            df_year = self.df[self.df['catalog_year'] == year].copy()
            degree_majors = sorted(df_year['degree_major'].dropna().unique())

            print(f"[menu_options] Year {year}: {len(degree_majors)} unique degree_major entries")

            for dm in degree_majors:
                # Back-compat structure
                self.year_degree_major_conc_options[year][dm] = []

                df_major = df_year[df_year['degree_major'] == dm]
                concentrations = list(
                    df_major['concentration']
                    .fillna('')
                    .drop_duplicates()
                    .tolist()
                )

                # fill back-compat
                self.year_degree_major_conc_options[year][dm].extend(concentrations)

                # Determine (degree_level, major_name) via mapping or heuristic
                if dm in self.code_map:
                    degree_level, major_name = self.code_map[dm]
                else:
                    degree_level, major_name = self._heuristic_split(dm)

                # Normalize nice labels: strip punctuationy leftovers
                degree_level = degree_level.strip()
                major_name = major_name.strip()

                # Populate new nested tree
                self.year_degree_major_conc_tree.setdefault(year, {})
                self.year_degree_major_conc_tree[year].setdefault(degree_level, {})
                self.year_degree_major_conc_tree[year][degree_level].setdefault(major_name, [])

                # Merge unique concentrations
                existing = set(self.year_degree_major_conc_tree[year][degree_level][major_name])
                for c in concentrations:
                    if c not in existing:
                        self.year_degree_major_conc_tree[year][degree_level][major_name].append(c)
                        existing.add(c)
