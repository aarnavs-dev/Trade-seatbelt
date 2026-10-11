"""Copy-trading follower: a normal TCP client of the gate. Paper only: an accepted order is a simulated full fill at its
price, exactly as the gate already does. It never opens gate.log; it writes only its own runs/<id>/copier.log."""
