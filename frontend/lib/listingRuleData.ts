// Written by `python -m app.cli free-tool-rules` from backend/app/pipeline/search_rules.py and
// backend/app/compliance/trademarks.py. Do not edit: tests/test_free_tool_rules.py compares them.
export const SUBJECTIVE = ["cute", "perfect", "beautiful", "unique", "amazing", "awesome", "adorable", "lovely", "gorgeous", "stunning", "premium", "high quality", "trendy", "must have", "stylish"] as const;
export const GIFT = ["gift", "gifts", "gift idea", "gift ideas", "present", "presents", "stocking stuffer", "stocking stuffers", "for her", "for him", "for mom", "for mum", "for dad", "for women", "for men", "for kids", "for girls", "for boys", "for wife", "for husband", "for grandma", "for grandpa", "for friends", "for friend", "for sister", "for brother", "for teachers", "for nurses", "for them", "for everyone"] as const;
export const COMMERCE = ["sale", "on sale", "discount", "% off", "percent off", "free shipping", "fast shipping", "shipping", "ships", "cheap", "deal", "clearance", "bogo", "coupon", "price", "bestseller", "best seller"] as const;
export const GARMENT_WORDS = ["crewneck", "hoodie", "pullover", "shirt", "sweatshirt", "tank", "tee", "tshirt"] as const;
export const STOP_WORDS = ["a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"] as const;
export const MAX_TITLE_WORDS = 14;
export const MAX_TITLE_PHRASES = 4;
export const MIN_MULTI_WORD_TAGS = 9;
export const TRADEMARKS = ["Disney", "Pixar", "Mickey", "Minnie Mouse", "Star Wars", "Mandalorian", "Baby Yoda", "Grogu", "Marvel", "Avengers", "Spider-Man", "Lilo and Stitch", "Winnie the Pooh", "Toy Story", "Lion King", "Disneyland", "Magic Kingdom", "Epcot", "World Showcase", "Harry Potter", "Hogwarts", "DC Comics", "Batman", "Superman", "Wonder Woman", "Looney Tunes", "Scooby Doo", "Nintendo", "Super Mario", "Legend of Zelda", "Pokemon", "Pikachu", "Minecraft", "Fortnite", "Barbie", "Hello Kitty", "Sanrio", "Snoopy", "Grinch", "Dr Seuss", "Sesame Street", "Paw Patrol", "Bluey", "Nike", "Adidas", "Under Armour", "Louis Vuitton", "Gucci", "Chanel", "NFL", "NBA", "MLB", "NHL", "Coca-Cola", "Starbucks"] as const;
export const TRADEMARKS_REVIEWED = "2026-10-31";
export const ORDINARY_WORDS = ["chanelle", "lionkingdom", "lionkingdoms", "marveled", "marveling", "marvelled", "marvelling", "marvellous", "marvellously", "marvelous", "marvelously", "marvelousness", "marvels", "scavenger", "scavengers"] as const;
export const JOINED_AFTER_FROM = 5;
export const JOINED_BEFORE_FROM = 6;
export const LOOSE_FROM = 4;
