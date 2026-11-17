const PRODUCTS = [
  {id: 1, title: "Logitech M331 Silent Plus Wireless Mouse, 2.4GHz", price: 1295},
  {id: 2, title: "HP X200 Wireless Mouse with 2.4 GHz receiver", price: 599},
  {id: 3, title: "Logitech MX Master 3S Performance Wireless Mouse", price: 9995},
  {id: 4, title: "Dell MS116 Wired Optical Mouse", price: 349},
  {id: 5, title: "Logitech K380 Multi-Device Bluetooth Keyboard", price: 2995},
];
const cart = () => JSON.parse(localStorage.getItem('cart') || '[]');
const saveCart = c => localStorage.setItem('cart', JSON.stringify(c));
function header() {
  const n = cart().reduce((a, i) => a + i.qty, 0);
  document.body.insertAdjacentHTML('afterbegin', `<header style="background:#131921;color:#fff;padding:10px;display:flex;gap:12px;align-items:center">
    <a href="index.html" style="color:#fff;font-weight:bold">DemoShop</a>
    <form action="results.html" style="flex:1;display:flex"><input name="q" type="search" aria-label="Search DemoShop" placeholder="Search DemoShop" style="flex:1;padding:6px">
    <input type="submit" value="Go" id="search-submit"></form>
    <a href="cart.html" style="color:#fff" aria-label="Cart, ${n} items">Cart (<span id="cart-count">${n}</span>)</a></header>`);
}
